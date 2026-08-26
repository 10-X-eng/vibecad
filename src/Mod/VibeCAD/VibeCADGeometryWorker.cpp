// SPDX-License-Identifier: LGPL-2.1-or-later

#include <algorithm>
#include <array>
#include <chrono>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <future>
#include <limits>
#include <queue>
#include <stdexcept>
#include <string>
#include <vector>

#include <BOPAlgo_ArgumentAnalyzer.hxx>
#include <BOPAlgo_ListOfCheckResult.hxx>
#include <BRepBndLib.hxx>
#include <BRepAdaptor_Curve.hxx>
#include <BRepAdaptor_Surface.hxx>
#include <BRepBuilderAPI_Copy.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <BRepCheck_ListOfStatus.hxx>
#include <BRepCheck_Result.hxx>
#include <BRepExtrema_DistShapeShape.hxx>
#include <BRepGProp.hxx>
#include <BRep_Tool.hxx>
#include <BRepTools.hxx>
#include <BRep_Builder.hxx>
#include <Bnd_Box.hxx>
#include <GeomAbs_CurveType.hxx>
#include <GeomAbs_SurfaceType.hxx>
#include <GProp_GProps.hxx>
#include <Message_ProgressIndicator.hxx>
#include <Precision.hxx>
#include <Standard_Failure.hxx>
#include <TopExp.hxx>
#include <TopTools_IndexedMapOfShape.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Edge.hxx>
#include <TopoDS_Face.hxx>
#include <TopoDS_Shape.hxx>
#include <TopoDS_Vertex.hxx>
#include <gp_Pnt.hxx>
#include <gp_Dir.hxx>
#include <gp_Vec.hxx>
#include <nlohmann/json.hpp>

namespace
{

using Clock = std::chrono::steady_clock;
using Json = nlohmann::json;

struct Vec3
{
    double x {0.0};
    double y {0.0};
    double z {0.0};
};

Vec3 operator+(const Vec3& lhs, const Vec3& rhs)
{
    return {lhs.x + rhs.x, lhs.y + rhs.y, lhs.z + rhs.z};
}

Vec3 operator-(const Vec3& lhs, const Vec3& rhs)
{
    return {lhs.x - rhs.x, lhs.y - rhs.y, lhs.z - rhs.z};
}

Vec3 operator*(const Vec3& value, double scalar)
{
    return {value.x * scalar, value.y * scalar, value.z * scalar};
}

double dot(const Vec3& lhs, const Vec3& rhs)
{
    return lhs.x * rhs.x + lhs.y * rhs.y + lhs.z * rhs.z;
}

Vec3 cross(const Vec3& lhs, const Vec3& rhs)
{
    return {
        lhs.y * rhs.z - lhs.z * rhs.y,
        lhs.z * rhs.x - lhs.x * rhs.z,
        lhs.x * rhs.y - lhs.y * rhs.x,
    };
}

double normSquared(const Vec3& value)
{
    return dot(value, value);
}

Json pointJson(const Vec3& point)
{
    return Json::array({point.x, point.y, point.z});
}

Json pointJson(const gp_Pnt& point)
{
    return Json::array({point.X(), point.Y(), point.Z()});
}

struct Triangle
{
    std::array<Vec3, 3> points;
};

struct Bounds
{
    Vec3 minimum {
        std::numeric_limits<double>::infinity(),
        std::numeric_limits<double>::infinity(),
        std::numeric_limits<double>::infinity(),
    };
    Vec3 maximum {
        -std::numeric_limits<double>::infinity(),
        -std::numeric_limits<double>::infinity(),
        -std::numeric_limits<double>::infinity(),
    };

    void add(const Vec3& point)
    {
        minimum.x = std::min(minimum.x, point.x);
        minimum.y = std::min(minimum.y, point.y);
        minimum.z = std::min(minimum.z, point.z);
        maximum.x = std::max(maximum.x, point.x);
        maximum.y = std::max(maximum.y, point.y);
        maximum.z = std::max(maximum.z, point.z);
    }

    void add(const Bounds& other)
    {
        add(other.minimum);
        add(other.maximum);
    }
};

Bounds triangleBounds(const Triangle& triangle)
{
    Bounds result;
    for (const Vec3& point : triangle.points) {
        result.add(point);
    }
    return result;
}

Vec3 triangleCenter(const Triangle& triangle)
{
    return (triangle.points[0] + triangle.points[1] + triangle.points[2]) * (1.0 / 3.0);
}

double boundsDistanceSquared(const Bounds& first, const Bounds& second)
{
    double result = 0.0;
    for (int axis = 0; axis < 3; ++axis) {
        const double firstMin = axis == 0 ? first.minimum.x
            : axis == 1                   ? first.minimum.y
                                          : first.minimum.z;
        const double firstMax = axis == 0 ? first.maximum.x
            : axis == 1                   ? first.maximum.y
                                          : first.maximum.z;
        const double secondMin = axis == 0 ? second.minimum.x
            : axis == 1                    ? second.minimum.y
                                           : second.minimum.z;
        const double secondMax = axis == 0 ? second.maximum.x
            : axis == 1                    ? second.maximum.y
                                           : second.maximum.z;
        double separation = 0.0;
        if (firstMax < secondMin) {
            separation = secondMin - firstMax;
        }
        else if (secondMax < firstMin) {
            separation = firstMin - secondMax;
        }
        result += separation * separation;
    }
    return result;
}

struct ClosestPair
{
    double distanceSquared {std::numeric_limits<double>::infinity()};
    Vec3 first;
    Vec3 second;
};

void consider(ClosestPair& best, const Vec3& first, const Vec3& second)
{
    const double candidate = normSquared(first - second);
    if (candidate < best.distanceSquared) {
        best = {candidate, first, second};
    }
}

Vec3 closestPointOnTriangle(const Vec3& point, const Triangle& triangle)
{
    const Vec3& a = triangle.points[0];
    const Vec3& b = triangle.points[1];
    const Vec3& c = triangle.points[2];
    const Vec3 ab = b - a;
    const Vec3 ac = c - a;
    const Vec3 ap = point - a;
    const double d1 = dot(ab, ap);
    const double d2 = dot(ac, ap);
    if (d1 <= 0.0 && d2 <= 0.0) {
        return a;
    }

    const Vec3 bp = point - b;
    const double d3 = dot(ab, bp);
    const double d4 = dot(ac, bp);
    if (d3 >= 0.0 && d4 <= d3) {
        return b;
    }

    const double vc = d1 * d4 - d3 * d2;
    if (vc <= 0.0 && d1 >= 0.0 && d3 <= 0.0) {
        const double v = d1 / (d1 - d3);
        return a + ab * v;
    }

    const Vec3 cp = point - c;
    const double d5 = dot(ab, cp);
    const double d6 = dot(ac, cp);
    if (d6 >= 0.0 && d5 <= d6) {
        return c;
    }

    const double vb = d5 * d2 - d1 * d6;
    if (vb <= 0.0 && d2 >= 0.0 && d6 <= 0.0) {
        const double w = d2 / (d2 - d6);
        return a + ac * w;
    }

    const double va = d3 * d6 - d5 * d4;
    if (va <= 0.0 && (d4 - d3) >= 0.0 && (d5 - d6) >= 0.0) {
        const Vec3 bc = c - b;
        const double w = (d4 - d3) / ((d4 - d3) + (d5 - d6));
        return b + bc * w;
    }

    const double denominator = 1.0 / (va + vb + vc);
    const double v = vb * denominator;
    const double w = vc * denominator;
    return a + ab * v + ac * w;
}

ClosestPair closestSegmentPair(
    const Vec3& firstStart,
    const Vec3& firstEnd,
    const Vec3& secondStart,
    const Vec3& secondEnd
)
{
    constexpr double epsilon = 1e-18;
    const Vec3 firstDirection = firstEnd - firstStart;
    const Vec3 secondDirection = secondEnd - secondStart;
    const Vec3 offset = firstStart - secondStart;
    const double firstLength = dot(firstDirection, firstDirection);
    const double secondLength = dot(secondDirection, secondDirection);
    const double mixed = dot(secondDirection, offset);
    double firstParameter = 0.0;
    double secondParameter = 0.0;

    if (firstLength <= epsilon && secondLength <= epsilon) {
        return {normSquared(firstStart - secondStart), firstStart, secondStart};
    }
    if (firstLength <= epsilon) {
        secondParameter = std::clamp(mixed / secondLength, 0.0, 1.0);
    }
    else {
        const double firstOffset = dot(firstDirection, offset);
        if (secondLength <= epsilon) {
            firstParameter = std::clamp(-firstOffset / firstLength, 0.0, 1.0);
        }
        else {
            const double coupling = dot(firstDirection, secondDirection);
            const double denominator = firstLength * secondLength - coupling * coupling;
            if (denominator > epsilon) {
                firstParameter = std::clamp(
                    (coupling * mixed - firstOffset * secondLength) / denominator,
                    0.0,
                    1.0
                );
            }
            secondParameter = (coupling * firstParameter + mixed) / secondLength;
            if (secondParameter < 0.0) {
                secondParameter = 0.0;
                firstParameter = std::clamp(-firstOffset / firstLength, 0.0, 1.0);
            }
            else if (secondParameter > 1.0) {
                secondParameter = 1.0;
                firstParameter = std::clamp((coupling - firstOffset) / firstLength, 0.0, 1.0);
            }
        }
    }
    const Vec3 firstPoint = firstStart + firstDirection * firstParameter;
    const Vec3 secondPoint = secondStart + secondDirection * secondParameter;
    return {normSquared(firstPoint - secondPoint), firstPoint, secondPoint};
}

bool segmentTriangleIntersection(
    const Vec3& start,
    const Vec3& end,
    const Triangle& triangle,
    Vec3& intersection
)
{
    constexpr double epsilon = 1e-12;
    const Vec3 direction = end - start;
    const Vec3 edge1 = triangle.points[1] - triangle.points[0];
    const Vec3 edge2 = triangle.points[2] - triangle.points[0];
    const Vec3 p = cross(direction, edge2);
    const double determinant = dot(edge1, p);
    if (std::abs(determinant) <= epsilon) {
        return false;
    }
    const double inverse = 1.0 / determinant;
    const Vec3 translated = start - triangle.points[0];
    const double u = dot(translated, p) * inverse;
    if (u < -epsilon || u > 1.0 + epsilon) {
        return false;
    }
    const Vec3 q = cross(translated, edge1);
    const double v = dot(direction, q) * inverse;
    if (v < -epsilon || u + v > 1.0 + epsilon) {
        return false;
    }
    const double parameter = dot(edge2, q) * inverse;
    if (parameter < -epsilon || parameter > 1.0 + epsilon) {
        return false;
    }
    intersection = start + direction * std::clamp(parameter, 0.0, 1.0);
    return true;
}

ClosestPair closestTrianglePair(const Triangle& first, const Triangle& second)
{
    ClosestPair best;
    for (int edge = 0; edge < 3; ++edge) {
        Vec3 intersection;
        if (segmentTriangleIntersection(
                first.points[edge],
                first.points[(edge + 1) % 3],
                second,
                intersection
            )) {
            return {0.0, intersection, intersection};
        }
        if (segmentTriangleIntersection(
                second.points[edge],
                second.points[(edge + 1) % 3],
                first,
                intersection
            )) {
            return {0.0, intersection, intersection};
        }
    }
    for (const Vec3& point : first.points) {
        consider(best, point, closestPointOnTriangle(point, second));
    }
    for (const Vec3& point : second.points) {
        consider(best, closestPointOnTriangle(point, first), point);
    }
    for (int firstEdge = 0; firstEdge < 3; ++firstEdge) {
        for (int secondEdge = 0; secondEdge < 3; ++secondEdge) {
            const ClosestPair candidate = closestSegmentPair(
                first.points[firstEdge],
                first.points[(firstEdge + 1) % 3],
                second.points[secondEdge],
                second.points[(secondEdge + 1) % 3]
            );
            consider(best, candidate.first, candidate.second);
        }
    }
    return best;
}

std::vector<Triangle> readStl(const std::filesystem::path& path)
{
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        throw std::runtime_error("Cannot open STL artifact: " + path.string());
    }
    input.seekg(0, std::ios::end);
    const std::streamoff size = input.tellg();
    input.seekg(80, std::ios::beg);
    std::uint32_t triangleCount = 0;
    input.read(reinterpret_cast<char*>(&triangleCount), sizeof(triangleCount));
    const std::uint64_t expected = 84ULL + 50ULL * triangleCount;
    if (input && size == static_cast<std::streamoff>(expected)) {
        std::vector<Triangle> triangles;
        triangles.reserve(triangleCount);
        input.seekg(84, std::ios::beg);
        for (std::uint32_t index = 0; index < triangleCount; ++index) {
            std::array<float, 12> record {};
            input.read(reinterpret_cast<char*>(record.data()), 48);
            std::uint16_t attribute = 0;
            input.read(reinterpret_cast<char*>(&attribute), sizeof(attribute));
            if (!input) {
                throw std::runtime_error("Binary STL ended before its declared triangle count.");
            }
            Triangle triangle;
            for (int vertex = 0; vertex < 3; ++vertex) {
                triangle.points[vertex] = {
                    record[3 + vertex * 3],
                    record[4 + vertex * 3],
                    record[5 + vertex * 3],
                };
            }
            triangles.push_back(triangle);
        }
        return triangles;
    }

    input.close();
    std::ifstream text(path);
    if (!text) {
        throw std::runtime_error("Cannot reopen ASCII STL artifact: " + path.string());
    }
    std::vector<Triangle> triangles;
    std::string token;
    std::vector<Vec3> vertices;
    while (text >> token) {
        if (token != "vertex") {
            continue;
        }
        Vec3 point;
        if (!(text >> point.x >> point.y >> point.z)) {
            throw std::runtime_error("ASCII STL contains an invalid vertex record.");
        }
        vertices.push_back(point);
        if (vertices.size() == 3) {
            triangles.push_back({{vertices[0], vertices[1], vertices[2]}});
            vertices.clear();
        }
    }
    if (triangles.empty() || !vertices.empty()) {
        throw std::runtime_error("STL artifact contains no complete triangles.");
    }
    return triangles;
}

struct BvhNode
{
    Bounds bounds;
    std::size_t begin {0};
    std::size_t count {0};
    int left {-1};
    int right {-1};

    bool leaf() const
    {
        return left < 0;
    }
};

class TriangleBvh
{
public:
    explicit TriangleBvh(std::vector<Triangle> input)
        : triangles(std::move(input))
    {
        if (triangles.empty()) {
            throw std::runtime_error("Cannot build a BVH for an empty mesh.");
        }
        order.resize(triangles.size());
        for (std::size_t index = 0; index < order.size(); ++index) {
            order[index] = index;
        }
        build(0, order.size());
    }

    std::vector<Triangle> triangles;
    std::vector<std::size_t> order;
    std::vector<BvhNode> nodes;

private:
    int build(std::size_t begin, std::size_t end)
    {
        const int nodeIndex = static_cast<int>(nodes.size());
        nodes.emplace_back();
        Bounds bounds;
        Bounds centers;
        for (std::size_t index = begin; index < end; ++index) {
            const Triangle& triangle = triangles[order[index]];
            bounds.add(triangleBounds(triangle));
            centers.add(triangleCenter(triangle));
        }
        nodes[nodeIndex].bounds = bounds;
        nodes[nodeIndex].begin = begin;
        nodes[nodeIndex].count = end - begin;
        if (end - begin <= 8) {
            return nodeIndex;
        }
        const Vec3 extent = centers.maximum - centers.minimum;
        const int axis = extent.x >= extent.y && extent.x >= extent.z ? 0
            : extent.y >= extent.z                                    ? 1
                                                                      : 2;
        const std::size_t middle = begin + (end - begin) / 2;
        std::nth_element(
            order.begin() + static_cast<std::ptrdiff_t>(begin),
            order.begin() + static_cast<std::ptrdiff_t>(middle),
            order.begin() + static_cast<std::ptrdiff_t>(end),
            [&](std::size_t lhs, std::size_t rhs) {
                const Vec3 leftCenter = triangleCenter(triangles[lhs]);
                const Vec3 rightCenter = triangleCenter(triangles[rhs]);
                return axis == 0 ? leftCenter.x < rightCenter.x
                    : axis == 1  ? leftCenter.y < rightCenter.y
                                 : leftCenter.z < rightCenter.z;
            }
        );
        const int left = build(begin, middle);
        const int right = build(middle, end);
        nodes[nodeIndex].left = left;
        nodes[nodeIndex].right = right;
        nodes[nodeIndex].count = 0;
        return nodeIndex;
    }
};

ClosestPair meshDistance(const TriangleBvh& first, const TriangleBvh& second)
{
    struct Candidate
    {
        double lowerBound;
        int firstNode;
        int secondNode;
    };
    struct FartherFirst
    {
        bool operator()(const Candidate& lhs, const Candidate& rhs) const
        {
            return lhs.lowerBound > rhs.lowerBound;
        }
    };
    std::priority_queue<Candidate, std::vector<Candidate>, FartherFirst> pending;
    pending.push({boundsDistanceSquared(first.nodes[0].bounds, second.nodes[0].bounds), 0, 0});
    ClosestPair best;
    while (!pending.empty()) {
        const Candidate candidate = pending.top();
        pending.pop();
        if (candidate.lowerBound >= best.distanceSquared) {
            continue;
        }
        const BvhNode& firstNode = first.nodes[candidate.firstNode];
        const BvhNode& secondNode = second.nodes[candidate.secondNode];
        if (firstNode.leaf() && secondNode.leaf()) {
            for (std::size_t firstOffset = 0; firstOffset < firstNode.count; ++firstOffset) {
                const Triangle& firstTriangle
                    = first.triangles[first.order[firstNode.begin + firstOffset]];
                for (std::size_t secondOffset = 0; secondOffset < secondNode.count; ++secondOffset) {
                    const Triangle& secondTriangle
                        = second.triangles[second.order[secondNode.begin + secondOffset]];
                    const ClosestPair measured = closestTrianglePair(firstTriangle, secondTriangle);
                    if (measured.distanceSquared < best.distanceSquared) {
                        best = measured;
                    }
                    if (best.distanceSquared <= 1e-24) {
                        return best;
                    }
                }
            }
            continue;
        }
        const auto enqueue = [&](int firstIndex, int secondIndex) {
            const double lower = boundsDistanceSquared(
                first.nodes[firstIndex].bounds,
                second.nodes[secondIndex].bounds
            );
            if (lower < best.distanceSquared) {
                pending.push({lower, firstIndex, secondIndex});
            }
        };
        if (firstNode.leaf()) {
            enqueue(candidate.firstNode, secondNode.left);
            enqueue(candidate.firstNode, secondNode.right);
        }
        else if (secondNode.leaf()) {
            enqueue(firstNode.left, candidate.secondNode);
            enqueue(firstNode.right, candidate.secondNode);
        }
        else {
            enqueue(firstNode.left, secondNode.left);
            enqueue(firstNode.left, secondNode.right);
            enqueue(firstNode.right, secondNode.left);
            enqueue(firstNode.right, secondNode.right);
        }
    }
    return best;
}

class DeadlineProgressIndicator final: public Message_ProgressIndicator
{
public:
    explicit DeadlineProgressIndicator(std::chrono::milliseconds timeout)
        : deadline(Clock::now() + timeout)
    {}

    void Show(const Message_ProgressScope&, const Standard_Boolean) override
    {}

    Standard_Boolean UserBreak() override
    {
        return Clock::now() >= deadline;
    }

private:
    Clock::time_point deadline;
};

TopoDS_Shape readBrep(const std::filesystem::path& path)
{
    TopoDS_Shape shape;
    BRep_Builder builder;
    if (!BRepTools::Read(shape, path.string().c_str(), builder) || shape.IsNull()) {
        throw std::runtime_error("Cannot read BREP artifact: " + path.string());
    }
    return shape;
}

int subshapeCount(const TopoDS_Shape& shape, TopAbs_ShapeEnum type)
{
    TopTools_IndexedMapOfShape map;
    TopExp::MapShapes(shape, type, map);
    return map.Extent();
}

Json shapeFacts(const TopoDS_Shape& shape)
{
    Bnd_Box box;
    BRepBndLib::AddOptimal(shape, box, false, false);
    Standard_Real xMin = 0.0;
    Standard_Real yMin = 0.0;
    Standard_Real zMin = 0.0;
    Standard_Real xMax = 0.0;
    Standard_Real yMax = 0.0;
    Standard_Real zMax = 0.0;
    box.Get(xMin, yMin, zMin, xMax, yMax, zMax);
    GProp_GProps volume;
    GProp_GProps area;
    BRepGProp::VolumeProperties(shape, volume);
    BRepGProp::SurfaceProperties(shape, area);
    return {
        {"valid", BRepCheck_Analyzer(shape, true).IsValid()},
        {"solids", subshapeCount(shape, TopAbs_SOLID)},
        {"faces", subshapeCount(shape, TopAbs_FACE)},
        {"edges", subshapeCount(shape, TopAbs_EDGE)},
        {"vertices", subshapeCount(shape, TopAbs_VERTEX)},
        {"volume_mm3", volume.Mass()},
        {"area_mm2", area.Mass()},
        {"bbox", {{"min", Json::array({xMin, yMin, zMin})}, {"max", Json::array({xMax, yMax, zMax})}}},
    };
}

Json boundsFacts(const TopoDS_Shape& shape)
{
    Bnd_Box box;
    // A conservative topology box is the useful contract here.  AddOptimal
    // analytically re-solves every surface and can turn a bounds read on a
    // large imported B-rep into a minute-long operation.  Add includes each
    // subshape's modeling tolerance, so it remains a safe enclosing box for
    // selector prefilters and placement decisions without that recomputation.
    BRepBndLib::Add(shape, box, true);
    box.SetGap(0.0);
    Standard_Real xMin = 0.0;
    Standard_Real yMin = 0.0;
    Standard_Real zMin = 0.0;
    Standard_Real xMax = 0.0;
    Standard_Real yMax = 0.0;
    Standard_Real zMax = 0.0;
    box.Get(xMin, yMin, zMin, xMax, yMax, zMax);
    return {
        {"min", Json::array({xMin, yMin, zMin})},
        {"max", Json::array({xMax, yMax, zMax})},
        {"size", Json::array({xMax - xMin, yMax - yMin, zMax - zMin})},
    };
}

std::string displayShapeTypeName(TopAbs_ShapeEnum type)
{
    static const std::array<const char*, 9> names {
        "Compound",
        "CompSolid",
        "Solid",
        "Shell",
        "Face",
        "Wire",
        "Edge",
        "Vertex",
        "Shape",
    };
    const std::size_t index = static_cast<std::size_t>(type);
    return index < names.size() ? names[index] : "Unknown";
}

std::string orientationName(TopAbs_Orientation orientation)
{
    switch (orientation) {
        case TopAbs_FORWARD:
            return "Forward";
        case TopAbs_REVERSED:
            return "Reversed";
        case TopAbs_INTERNAL:
            return "Internal";
        case TopAbs_EXTERNAL:
            return "External";
    }
    return "Unknown";
}

std::string surfaceTypeName(GeomAbs_SurfaceType type)
{
    switch (type) {
        case GeomAbs_Plane:
            return "Plane";
        case GeomAbs_Cylinder:
            return "Cylinder";
        case GeomAbs_Cone:
            return "Cone";
        case GeomAbs_Sphere:
            return "Sphere";
        case GeomAbs_Torus:
            return "Torus";
        case GeomAbs_BezierSurface:
            return "BezierSurface";
        case GeomAbs_BSplineSurface:
            return "BSplineSurface";
        case GeomAbs_SurfaceOfRevolution:
            return "SurfaceOfRevolution";
        case GeomAbs_SurfaceOfExtrusion:
            return "SurfaceOfExtrusion";
        case GeomAbs_OffsetSurface:
            return "OffsetSurface";
        case GeomAbs_OtherSurface:
            return "OtherSurface";
    }
    return "Undefined";
}

std::string curveTypeName(GeomAbs_CurveType type)
{
    switch (type) {
        case GeomAbs_Line:
            return "Line";
        case GeomAbs_Circle:
            return "Circle";
        case GeomAbs_Ellipse:
            return "Ellipse";
        case GeomAbs_Hyperbola:
            return "Hyperbola";
        case GeomAbs_Parabola:
            return "Parabola";
        case GeomAbs_BezierCurve:
            return "BezierCurve";
        case GeomAbs_BSplineCurve:
            return "BSplineCurve";
        case GeomAbs_OffsetCurve:
            return "OffsetCurve";
        case GeomAbs_OtherCurve:
            return "OtherCurve";
    }
    return "Undefined";
}

Json directionJson(const gp_Dir& direction)
{
    return Json::array({direction.X(), direction.Y(), direction.Z()});
}

Json directionJson(const gp_Vec& direction)
{
    return Json::array({direction.X(), direction.Y(), direction.Z()});
}

Json faceFacts(int index, const TopoDS_Face& face)
{
    BRepAdaptor_Surface surface(face, true);
    const std::string geometryType = surfaceTypeName(surface.GetType());
    GProp_GProps properties;
    BRepGProp::SurfaceProperties(face, properties);
    Json normal = nullptr;
    try {
        Standard_Real uMin = 0.0;
        Standard_Real uMax = 0.0;
        Standard_Real vMin = 0.0;
        Standard_Real vMax = 0.0;
        BRepTools::UVBounds(face, uMin, uMax, vMin, vMax);
        gp_Pnt point;
        gp_Vec du;
        gp_Vec dv;
        surface.D1((uMin + uMax) / 2.0, (vMin + vMax) / 2.0, point, du, dv);
        gp_Vec value = du.Crossed(dv);
        if (value.Magnitude() > Precision::Confusion()) {
            value.Normalize();
            if (face.Orientation() == TopAbs_REVERSED) {
                value.Reverse();
            }
            normal = Json::array({value.X(), value.Y(), value.Z()});
        }
    }
    catch (const Standard_Failure&) {
        normal = nullptr;
    }
    Json result {
        {"index", index},
        {"geometry_type", geometryType},
        {"surface_type", geometryType},
        {"orientation", orientationName(face.Orientation())},
        {"area_mm2", properties.Mass()},
        {"center_mm", pointJson(properties.CentreOfMass())},
        {"bounds_mm", boundsFacts(face)},
        {"edge_count", subshapeCount(face, TopAbs_EDGE)},
        {"wire_count", subshapeCount(face, TopAbs_WIRE)},
        {"normal", normal},
        {"normal_at_center", normal},
    };
    switch (surface.GetType()) {
        case GeomAbs_Plane: {
            const auto plane = surface.Plane();
            result["origin_mm"] = pointJson(plane.Location());
            result["axis_direction"] = directionJson(plane.Axis().Direction());
            result["x_direction"] = directionJson(plane.Position().XDirection());
            break;
        }
        case GeomAbs_Cylinder: {
            const auto cylinder = surface.Cylinder();
            result["origin_mm"] = pointJson(cylinder.Location());
            result["axis_direction"] = directionJson(cylinder.Axis().Direction());
            result["x_direction"] = directionJson(cylinder.Position().XDirection());
            result["radius_mm"] = cylinder.Radius();
            break;
        }
        case GeomAbs_Cone: {
            const auto cone = surface.Cone();
            result["origin_mm"] = pointJson(cone.Location());
            result["axis_direction"] = directionJson(cone.Axis().Direction());
            result["x_direction"] = directionJson(cone.Position().XDirection());
            result["reference_radius_mm"] = cone.RefRadius();
            constexpr double radiansToDegrees = 57.2957795130823208768;
            result["semi_angle_degrees"] = cone.SemiAngle() * radiansToDegrees;
            break;
        }
        case GeomAbs_Sphere: {
            const auto sphere = surface.Sphere();
            result["origin_mm"] = pointJson(sphere.Location());
            result["axis_direction"] = directionJson(sphere.Position().Direction());
            result["x_direction"] = directionJson(sphere.Position().XDirection());
            result["radius_mm"] = sphere.Radius();
            break;
        }
        case GeomAbs_Torus: {
            const auto torus = surface.Torus();
            result["origin_mm"] = pointJson(torus.Location());
            result["axis_direction"] = directionJson(torus.Axis().Direction());
            result["x_direction"] = directionJson(torus.Position().XDirection());
            result["major_radius_mm"] = torus.MajorRadius();
            result["minor_radius_mm"] = torus.MinorRadius();
            break;
        }
        default:
            break;
    }
    return result;
}

Json edgeFacts(int index, const TopoDS_Edge& edge)
{
    BRepAdaptor_Curve curve(edge);
    const std::string geometryType = curveTypeName(curve.GetType());
    TopoDS_Vertex first;
    TopoDS_Vertex last;
    TopExp::Vertices(edge, first, last, true);
    Json endpoints = Json::array();
    if (!first.IsNull()) {
        endpoints.push_back(pointJson(BRep_Tool::Pnt(first)));
    }
    if (!last.IsNull() && (first.IsNull() || !last.IsSame(first))) {
        endpoints.push_back(pointJson(BRep_Tool::Pnt(last)));
    }
    GProp_GProps properties;
    BRepGProp::LinearProperties(edge, properties);
    Json direction = nullptr;
    try {
        const Standard_Real firstParameter = curve.FirstParameter();
        const Standard_Real lastParameter = curve.LastParameter();
        if (std::isfinite(firstParameter) && std::isfinite(lastParameter)) {
            gp_Pnt point;
            gp_Vec tangent;
            curve.D1((firstParameter + lastParameter) / 2.0, point, tangent);
            if (tangent.Magnitude() > Precision::Confusion()) {
                tangent.Normalize();
                if (edge.Orientation() == TopAbs_REVERSED) {
                    tangent.Reverse();
                }
                direction = directionJson(tangent);
            }
        }
    }
    catch (const Standard_Failure&) {
        direction = nullptr;
    }
    Json result {
        {"index", index},
        {"geometry_type", geometryType},
        {"curve_type", geometryType},
        {"orientation", orientationName(edge.Orientation())},
        {"length_mm", properties.Mass()},
        {"center_mm", pointJson(properties.CentreOfMass())},
        {"bounds_mm", boundsFacts(edge)},
        {"endpoints_mm", std::move(endpoints)},
        {"closed", BRep_Tool::IsClosed(edge)},
        {"direction", std::move(direction)},
    };
    switch (curve.GetType()) {
        case GeomAbs_Line: {
            const auto line = curve.Line();
            result["origin_mm"] = pointJson(line.Location());
            result["axis_direction"] = directionJson(line.Direction());
            break;
        }
        case GeomAbs_Circle: {
            const auto circle = curve.Circle();
            result["origin_mm"] = pointJson(circle.Location());
            result["axis_direction"] = directionJson(circle.Axis().Direction());
            result["x_direction"] = directionJson(circle.Position().XDirection());
            result["radius_mm"] = circle.Radius();
            break;
        }
        case GeomAbs_Ellipse: {
            const auto ellipse = curve.Ellipse();
            result["origin_mm"] = pointJson(ellipse.Location());
            result["axis_direction"] = directionJson(ellipse.Axis().Direction());
            result["x_direction"] = directionJson(ellipse.Position().XDirection());
            result["major_radius_mm"] = ellipse.MajorRadius();
            result["minor_radius_mm"] = ellipse.MinorRadius();
            break;
        }
        default:
            break;
    }
    return result;
}

std::string lowerText(std::string value)
{
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char character) {
        return static_cast<char>(std::tolower(character));
    });
    return value;
}

bool directionMatches(const Json& actual, const Json& requested, double toleranceDegrees)
{
    if (!actual.is_array() || actual.size() != 3 || !requested.is_array() || requested.size() != 3) {
        return false;
    }
    const gp_Vec left(actual[0].get<double>(), actual[1].get<double>(), actual[2].get<double>());
    const gp_Vec right(
        requested[0].get<double>(),
        requested[1].get<double>(),
        requested[2].get<double>()
    );
    if (left.Magnitude() <= Precision::Confusion() || right.Magnitude() <= Precision::Confusion()) {
        return false;
    }
    const double cosine
        = std::clamp(left.Dot(right) / (left.Magnitude() * right.Magnitude()), -1.0, 1.0);
    constexpr double radiansToDegrees = 57.2957795130823208768;
    return std::acos(cosine) * radiansToDegrees <= toleranceDegrees;
}

bool numericRangeMatches(
    const Json& facts,
    const Json& query,
    const char* factName,
    const char* minimumName,
    const char* maximumName
)
{
    if (!query.contains(minimumName) && !query.contains(maximumName)) {
        return true;
    }
    if (!facts.contains(factName) || !facts.at(factName).is_number()) {
        return false;
    }
    const double value = facts.at(factName).get<double>();
    return (!query.contains(minimumName) || value >= query.at(minimumName).get<double>())
        && (!query.contains(maximumName) || value <= query.at(maximumName).get<double>());
}

bool geometryQueryMatches(const Json& facts, const Json& query)
{
    if (query.contains("geometry_type")
        && lowerText(facts.value("geometry_type", ""))
            != lowerText(query.at("geometry_type").get<std::string>())) {
        return false;
    }
    const double angleTolerance = query.value("angle_tolerance_degrees", 1.0);
    for (const char* field : {"normal", "direction", "axis_direction"}) {
        if (query.contains(field)
            && (!facts.contains(field)
                || !directionMatches(facts.at(field), query.at(field), angleTolerance))) {
            return false;
        }
    }
    if (query.contains("radius_mm")) {
        if (!facts.contains("radius_mm") || !facts.at("radius_mm").is_number()) {
            return false;
        }
        const double tolerance = query.value("radius_tolerance_mm", 1.0e-6);
        if (std::abs(facts.at("radius_mm").get<double>() - query.at("radius_mm").get<double>())
            > tolerance) {
            return false;
        }
    }
    if (!numericRangeMatches(facts, query, "area_mm2", "min_area_mm2", "max_area_mm2")
        || !numericRangeMatches(facts, query, "length_mm", "min_length_mm", "max_length_mm")) {
        return false;
    }
    if (query.contains("near_point_mm")) {
        if (!facts.contains("center_mm") || !facts.at("center_mm").is_array()) {
            return false;
        }
        const Json& center = facts.at("center_mm");
        const Json& point = query.at("near_point_mm");
        if (center.size() != 3 || !point.is_array() || point.size() != 3) {
            return false;
        }
        double distanceSquared = 0.0;
        for (std::size_t index = 0; index < 3; ++index) {
            const double difference = center[index].get<double>() - point[index].get<double>();
            distanceSquared += difference * difference;
        }
        const double maximumDistance = query.value("max_distance_mm", 1.0e-6);
        if (std::sqrt(distanceSquared) > maximumDistance) {
            return false;
        }
    }
    return true;
}

bool centerDistanceBoundsMayMatch(const TopoDS_Shape& shape, const Json& query)
{
    if (!query.contains("near_point_mm")) {
        return true;
    }
    const Json& point = query.at("near_point_mm");
    if (!point.is_array() || point.size() != 3) {
        return false;
    }
    Bnd_Box box;
    BRepBndLib::Add(shape, box, false);
    if (box.IsVoid()) {
        return false;
    }
    Standard_Real xMin = 0.0;
    Standard_Real yMin = 0.0;
    Standard_Real zMin = 0.0;
    Standard_Real xMax = 0.0;
    Standard_Real yMax = 0.0;
    Standard_Real zMax = 0.0;
    box.Get(xMin, yMin, zMin, xMax, yMax, zMax);
    const std::array<double, 3> minimum {xMin, yMin, zMin};
    const std::array<double, 3> maximum {xMax, yMax, zMax};
    double distanceSquared = 0.0;
    for (std::size_t index = 0; index < 3; ++index) {
        const double coordinate = point[index].get<double>();
        const double nearest = std::clamp(coordinate, minimum[index], maximum[index]);
        const double difference = coordinate - nearest;
        distanceSquared += difference * difference;
    }
    const double maximumDistance = query.value("max_distance_mm", 1.0e-6);
    return distanceSquared <= maximumDistance * maximumDistance;
}

bool radiusMatches(double actual, const Json& query)
{
    if (!query.contains("radius_mm")) {
        return true;
    }
    const double tolerance = query.value("radius_tolerance_mm", 1.0e-6);
    return std::abs(actual - query.at("radius_mm").get<double>()) <= tolerance;
}

bool faceQueryMayMatch(const TopoDS_Face& face, const Json& query)
{
    BRepAdaptor_Surface surface(face, true);
    const std::string geometryType = surfaceTypeName(surface.GetType());
    if (query.contains("geometry_type")
        && lowerText(geometryType) != lowerText(query.at("geometry_type").get<std::string>())) {
        return false;
    }
    if (query.contains("radius_mm")) {
        double radius = 0.0;
        if (surface.GetType() == GeomAbs_Cylinder) {
            radius = surface.Cylinder().Radius();
        }
        else if (surface.GetType() == GeomAbs_Sphere) {
            radius = surface.Sphere().Radius();
        }
        else {
            return false;
        }
        if (!radiusMatches(radius, query)) {
            return false;
        }
    }
    return centerDistanceBoundsMayMatch(face, query);
}

bool edgeQueryMayMatch(const TopoDS_Edge& edge, const Json& query)
{
    BRepAdaptor_Curve curve(edge);
    const std::string geometryType = curveTypeName(curve.GetType());
    if (query.contains("geometry_type")
        && lowerText(geometryType) != lowerText(query.at("geometry_type").get<std::string>())) {
        return false;
    }
    if (query.contains("radius_mm")) {
        if (curve.GetType() != GeomAbs_Circle || !radiusMatches(curve.Circle().Radius(), query)) {
            return false;
        }
    }
    return centerDistanceBoundsMayMatch(edge, query);
}

void recordGeometryQueryMatch(Json& result, const Json& query, const Json& facts)
{
    if (!geometryQueryMatches(facts, query)) {
        return;
    }
    const int matchedCount = result.value("matched_count", 0) + 1;
    result["matched_count"] = matchedCount;
    const int resultLimit = std::clamp(query.value("max_results", 16), 1, 16);
    if (static_cast<int>(result.at("matches").size()) < resultLimit) {
        result["matches"].push_back(facts);
    }
}

Json inspectBrep(const Json& request)
{
    const TopoDS_Shape shape = readBrep(request.at("shape").at("path").get<std::string>());
    const std::string analysisLevel = request.value("analysis_level", "full");
    if (analysisLevel != "topology" && analysisLevel != "full") {
        throw std::runtime_error("Shape inspection analysis_level must be topology or full.");
    }
    const bool includeGlobalProperties = analysisLevel == "full";
    const int requestedLimit = request.value("max_subelements", 0);
    const int detailLimit = std::clamp(requestedLimit, 0, 32);
    const Json queries = request.value("queries", Json::array());
    if (!queries.is_array() || queries.size() > 16) {
        throw std::runtime_error("Shape inspection accepts at most 16 geometry queries.");
    }

    TopTools_IndexedMapOfShape solids;
    TopTools_IndexedMapOfShape shells;
    TopTools_IndexedMapOfShape faces;
    TopTools_IndexedMapOfShape wires;
    TopTools_IndexedMapOfShape edges;
    TopTools_IndexedMapOfShape vertices;
    TopExp::MapShapes(shape, TopAbs_SOLID, solids);
    TopExp::MapShapes(shape, TopAbs_SHELL, shells);
    TopExp::MapShapes(shape, TopAbs_FACE, faces);
    TopExp::MapShapes(shape, TopAbs_WIRE, wires);
    TopExp::MapShapes(shape, TopAbs_EDGE, edges);
    TopExp::MapShapes(shape, TopAbs_VERTEX, vertices);

    std::future<GProp_GProps> linearFuture;
    std::future<GProp_GProps> surfaceFuture;
    std::future<GProp_GProps> volumeFuture;
    std::future<bool> validityFuture;
    if (includeGlobalProperties) {
        linearFuture = std::async(std::launch::async, [shape]() {
            GProp_GProps properties;
            BRepGProp::LinearProperties(shape, properties);
            return properties;
        });
        surfaceFuture = std::async(std::launch::async, [shape]() {
            GProp_GProps properties;
            BRepGProp::SurfaceProperties(shape, properties);
            return properties;
        });
        volumeFuture = std::async(std::launch::async, [shape]() {
            GProp_GProps properties;
            BRepGProp::VolumeProperties(shape, properties);
            return properties;
        });
        validityFuture = std::async(std::launch::async, [shape]() {
            return BRepCheck_Analyzer(shape, true).IsValid();
        });
    }
    const Json bounds = boundsFacts(shape);
    const auto& minimum = bounds.at("min");
    const auto& maximum = bounds.at("max");

    Json queryResults = Json::array();
    bool queryFaces = false;
    bool queryEdges = false;
    for (const Json& query : queries) {
        const std::string elementType = query.value("element_type", "");
        if (elementType != "face" && elementType != "edge") {
            throw std::runtime_error("A geometry query element_type must be face or edge.");
        }
        queryFaces = queryFaces || elementType == "face";
        queryEdges = queryEdges || elementType == "edge";
        queryResults.push_back({
            {"name", query.value("name", "")},
            {"element_type", elementType},
            {"matched_count", 0},
            {"matches", Json::array()},
        });
    }

    Json faceDetails = Json::array();
    const int inspectedFaceCount = queryFaces ? faces.Extent()
                                              : std::min(detailLimit, faces.Extent());
    for (int index = 1; index <= inspectedFaceCount; ++index) {
        const TopoDS_Face face = TopoDS::Face(faces(index));
        std::vector<std::size_t> candidateQueries;
        if (queryFaces) {
            for (std::size_t queryIndex = 0; queryIndex < queries.size(); ++queryIndex) {
                if (queries[queryIndex].value("element_type", "") == "face"
                    && faceQueryMayMatch(face, queries[queryIndex])) {
                    candidateQueries.push_back(queryIndex);
                }
            }
        }
        if (index > detailLimit && candidateQueries.empty()) {
            continue;
        }
        const Json facts = faceFacts(index, face);
        if (index <= detailLimit) {
            faceDetails.push_back(facts);
        }
        for (const std::size_t queryIndex : candidateQueries) {
            recordGeometryQueryMatch(queryResults[queryIndex], queries[queryIndex], facts);
        }
    }
    Json edgeDetails = Json::array();
    const int inspectedEdgeCount = queryEdges ? edges.Extent()
                                              : std::min(detailLimit, edges.Extent());
    for (int index = 1; index <= inspectedEdgeCount; ++index) {
        const TopoDS_Edge edge = TopoDS::Edge(edges(index));
        std::vector<std::size_t> candidateQueries;
        if (queryEdges) {
            for (std::size_t queryIndex = 0; queryIndex < queries.size(); ++queryIndex) {
                if (queries[queryIndex].value("element_type", "") == "edge"
                    && edgeQueryMayMatch(edge, queries[queryIndex])) {
                    candidateQueries.push_back(queryIndex);
                }
            }
        }
        if (index > detailLimit && candidateQueries.empty()) {
            continue;
        }
        const Json facts = edgeFacts(index, edge);
        if (index <= detailLimit) {
            edgeDetails.push_back(facts);
        }
        for (const std::size_t queryIndex : candidateQueries) {
            recordGeometryQueryMatch(queryResults[queryIndex], queries[queryIndex], facts);
        }
    }
    for (std::size_t queryIndex = 0; queryIndex < queries.size(); ++queryIndex) {
        Json& result = queryResults[queryIndex];
        const Json& query = queries[queryIndex];
        result["matches_truncated"] = result.at("matched_count").get<int>()
            > static_cast<int>(result.at("matches").size());
        if (query.contains("expected_count")) {
            const int expectedCount = query.at("expected_count").get<int>();
            result["expected_count"] = expectedCount;
            result["cardinality_ok"] = result.at("matched_count").get<int>() == expectedCount;
        }
    }

    Json geometry {
        {"analysis_level", analysisLevel},
        {"shape_type", displayShapeTypeName(shape.ShapeType())},
        {"null", shape.IsNull()},
        {"solids", solids.Extent()},
        {"shells", shells.Extent()},
        {"faces", faces.Extent()},
        {"wires", wires.Extent()},
        {"edges", edges.Extent()},
        {"vertices", vertices.Extent()},
        {"bounds_center_mm",
         Json::array(
             {(minimum[0].get<double>() + maximum[0].get<double>()) / 2.0,
              (minimum[1].get<double>() + maximum[1].get<double>()) / 2.0,
              (minimum[2].get<double>() + maximum[2].get<double>()) / 2.0}
         )},
        {"bounds_mm", bounds},
        {"face_details", std::move(faceDetails)},
        {"edge_details", std::move(edgeDetails)},
        {"query_results", std::move(queryResults)},
        {"subelement_detail_limit", detailLimit},
        {"subelement_details_truncated",
         faces.Extent() > detailLimit || edges.Extent() > detailLimit},
    };
    if (includeGlobalProperties) {
        const GProp_GProps linear = linearFuture.get();
        const GProp_GProps surface = surfaceFuture.get();
        const GProp_GProps volume = volumeFuture.get();
        const bool valid = validityFuture.get();
        const TopAbs_ShapeEnum centerType = solids.Extent() > 0 ? TopAbs_SOLID
            : faces.Extent() > 0                                ? TopAbs_FACE
                                                                : TopAbs_EDGE;
        const gp_Pnt center = centerType == TopAbs_SOLID ? volume.CentreOfMass()
            : centerType == TopAbs_FACE                  ? surface.CentreOfMass()
                                                         : linear.CentreOfMass();
        geometry["valid"] = valid;
        geometry["length_mm"] = linear.Mass();
        geometry["area_mm2"] = surface.Mass();
        geometry["volume_mm3"] = volume.Mass();
        geometry["center_of_mass_mm"] = pointJson(center);
    }

    return {
        {"ok", true},
        {"operation", "inspect_brep"},
        {"geometry", std::move(geometry)},
    };
}

std::string shapeTypeName(TopAbs_ShapeEnum type)
{
    static const std::array<const char*, 9> names {
        "compound",
        "compsolid",
        "solid",
        "shell",
        "face",
        "wire",
        "edge",
        "vertex",
        "shape",
    };
    const std::size_t index = static_cast<std::size_t>(type);
    return index < names.size() ? names[index] : "unknown";
}

std::string brepStatusName(BRepCheck_Status status)
{
    static const std::array<const char*, 34> names {
        "NoError",
        "InvalidPointOnCurve",
        "InvalidPointOnCurveOnSurface",
        "InvalidPointOnSurface",
        "No3DCurve",
        "Multiple3DCurve",
        "Invalid3DCurve",
        "NoCurveOnSurface",
        "InvalidCurveOnSurface",
        "InvalidCurveOnClosedSurface",
        "InvalidSameRangeFlag",
        "InvalidSameParameterFlag",
        "InvalidDegeneratedFlag",
        "FreeEdge",
        "InvalidMultiConnexity",
        "InvalidRange",
        "EmptyWire",
        "RedundantEdge",
        "SelfIntersectingWire",
        "NoSurface",
        "InvalidWire",
        "RedundantWire",
        "IntersectingWires",
        "InvalidImbricationOfWires",
        "EmptyShell",
        "RedundantFace",
        "UnorientableShape",
        "NotClosed",
        "NotConnected",
        "SubshapeNotInShape",
        "BadOrientation",
        "BadOrientationOfSubshape",
        "InvalidToleranceValue",
        "CheckFail",
    };
    const std::size_t index = static_cast<std::size_t>(status);
    return index < names.size() ? names[index] : "Unknown";
}

std::string bopStatusName(BOPAlgo_CheckStatus status)
{
    static const std::array<const char*, 12> names {
        "CheckUnknown",
        "BadType",
        "SelfIntersect",
        "TooSmallEdge",
        "NonRecoverableFace",
        "IncompatibilityOfVertex",
        "IncompatibilityOfEdge",
        "IncompatibilityOfFace",
        "OperationAborted",
        "GeomAbs_C0",
        "InvalidCurveOnSurface",
        "NotValid",
    };
    const std::size_t index = static_cast<std::size_t>(status);
    return index < names.size() ? names[index] : "Unknown";
}

Json brepDefects(const TopoDS_Shape& shape, BRepCheck_Analyzer& analyzer)
{
    static const std::array<TopAbs_ShapeEnum, 8> checkedTypes {
        TopAbs_VERTEX,
        TopAbs_EDGE,
        TopAbs_WIRE,
        TopAbs_FACE,
        TopAbs_SHELL,
        TopAbs_SOLID,
        TopAbs_COMPOUND,
        TopAbs_COMPSOLID,
    };
    Json defects = Json::array();
    for (const TopAbs_ShapeEnum type : checkedTypes) {
        TopTools_IndexedMapOfShape subshapes;
        TopExp::MapShapes(shape, type, subshapes);
        for (int index = 1; index <= subshapes.Extent(); ++index) {
            const TopoDS_Shape& subshape = subshapes(index);
            if (analyzer.IsValid(subshape)) {
                continue;
            }
            const Handle(BRepCheck_Result)& result = analyzer.Result(subshape);
            if (result.IsNull()) {
                defects.push_back(
                    {{"shape_type", shapeTypeName(type)},
                     {"shape_index", index},
                     {"status", "Unknown"},
                     {"status_code", -1}}
                );
                continue;
            }
            const BRepCheck_ListOfStatus& statuses = result->StatusOnShape(subshape);
            for (BRepCheck_ListIteratorOfListOfStatus iterator(statuses); iterator.More();
                 iterator.Next()) {
                const BRepCheck_Status status = iterator.Value();
                if (status == BRepCheck_NoError) {
                    continue;
                }
                defects.push_back(
                    {{"shape_type", shapeTypeName(type)},
                     {"shape_index", index},
                     {"status", brepStatusName(status)},
                     {"status_code", static_cast<int>(status)}}
                );
            }
        }
    }
    return defects;
}

void appendBopFaults(
    Json& defects,
    const BOPAlgo_CheckResult& result,
    const TopTools_ListOfShape& shapes,
    int argument
)
{
    int index = 0;
    for (TopTools_ListIteratorOfListOfShape iterator(shapes); iterator.More(); iterator.Next()) {
        ++index;
        const TopoDS_Shape& shape = iterator.Value();
        defects.push_back(
            {{"argument", argument},
             {"shape_type", shapeTypeName(shape.ShapeType())},
             {"shape_index", index},
             {"status", bopStatusName(result.GetCheckStatus())},
             {"status_code", static_cast<int>(result.GetCheckStatus())}}
        );
    }
}

Json bopDefects(const TopoDS_Shape& shape)
{
    const TopoDS_Shape copiedShape = BRepBuilderAPI_Copy(shape).Shape();
    BOPAlgo_ArgumentAnalyzer analyzer;
    analyzer.SetShape1(copiedShape);
    analyzer.ArgumentTypeMode() = true;
    analyzer.SelfInterMode() = true;
    analyzer.SmallEdgeMode() = true;
    analyzer.RebuildFaceMode() = true;
    analyzer.ContinuityMode() = true;
    analyzer.SetParallelMode(true);
    analyzer.SetRunParallel(true);
    analyzer.TangentMode() = true;
    analyzer.MergeVertexMode() = true;
    analyzer.CurveOnSurfaceMode() = true;
    analyzer.MergeEdgeMode() = true;
    analyzer.Perform();

    Json defects = Json::array();
    for (BOPAlgo_ListIteratorOfListOfCheckResult iterator(analyzer.GetCheckResult()); iterator.More();
         iterator.Next()) {
        const BOPAlgo_CheckResult& result = iterator.Value();
        const std::size_t previousSize = defects.size();
        appendBopFaults(defects, result, result.GetFaultyShapes1(), 1);
        appendBopFaults(defects, result, result.GetFaultyShapes2(), 2);
        if (defects.size() == previousSize) {
            defects.push_back(
                {{"argument", 0},
                 {"shape_type", "unknown"},
                 {"shape_index", 0},
                 {"status", bopStatusName(result.GetCheckStatus())},
                 {"status_code", static_cast<int>(result.GetCheckStatus())}}
            );
        }
    }
    return defects;
}

Json validateBrep(const Json& request)
{
    const TopoDS_Shape shape = readBrep(request.at("shape").at("path").get<std::string>());
    BRepCheck_Analyzer brepAnalyzer(shape);
    const bool brepValid = brepAnalyzer.IsValid();
    const bool includeBop = request.value("include_bop", true);
    Json brep = {
        {"valid", brepValid},
        {"defects", brepValid ? Json::array() : brepDefects(shape, brepAnalyzer)},
    };

    Json bop;
    bool valid = brepValid;
    if (brepValid && includeBop) {
        Json defects = bopDefects(shape);
        valid = defects.empty();
        bop = {
            {"performed", true},
            {"valid", valid},
            {"defects", std::move(defects)},
        };
    }
    else if (!brepValid) {
        bop = {
            {"performed", false},
            {"valid", nullptr},
            {"defects", Json::array()},
            {"reason", "BOP analysis skipped because BRepCheck rejected the shape."},
        };
    }
    else {
        bop = {
            {"performed", false},
            {"valid", nullptr},
            {"defects", Json::array()},
            {"reason", "BOP analysis was not requested for this BREP validity check."},
        };
    }
    return {
        {"ok", true},
        {"operation", "validate_brep"},
        {"valid", valid},
        {"brep", std::move(brep)},
        {"bop", std::move(bop)},
    };
}

Json brepMinimumDistance(const Json& request)
{
    const TopoDS_Shape first = readBrep(request.at("first").at("path").get<std::string>());
    const TopoDS_Shape second = readBrep(request.at("second").at("path").get<std::string>());
    const double tolerance = request.value("tolerance", Precision::Confusion());
    const auto timeout = std::chrono::milliseconds(request.value("deadline_ms", 30000));
    Handle(DeadlineProgressIndicator) progress = new DeadlineProgressIndicator(timeout);
    BRepExtrema_DistShapeShape extrema;
    extrema.SetDeflection(tolerance);
    extrema.SetMultiThread(true);
    extrema.LoadS1(first);
    extrema.LoadS2(second);
    extrema.Perform(Message_ProgressIndicator::Start(progress));
    if (progress->UserBreak()) {
        throw std::runtime_error("Geometry distance exceeded its native deadline.");
    }
    if (!extrema.IsDone() || extrema.NbSolution() < 1) {
        throw std::runtime_error("BRepExtrema_DistShapeShape returned no solution.");
    }
    Json pairs = Json::array();
    for (int index = 1; index <= extrema.NbSolution(); ++index) {
        pairs.push_back(
            {{"first", pointJson(extrema.PointOnShape1(index))},
             {"second", pointJson(extrema.PointOnShape2(index))}}
        );
    }
    return {
        {"ok", true},
        {"fidelity", request.value("fidelity", "exact_brep")},
        {"calculation", "isolated_opencascade_bounded_shape_to_shape"},
        {"distance", extrema.Value()},
        {"closest_point_pairs", pairs},
        {"first_shape", shapeFacts(first)},
        {"second_shape", shapeFacts(second)},
    };
}

Json stlMinimumDistance(const Json& request)
{
    const TriangleBvh first(readStl(request.at("first").at("path").get<std::string>()));
    const TriangleBvh second(readStl(request.at("second").at("path").get<std::string>()));
    const ClosestPair measured = meshDistance(first, second);
    if (!std::isfinite(measured.distanceSquared)) {
        throw std::runtime_error("The faceted distance solver returned no solution.");
    }
    return {
        {"ok", true},
        {"fidelity", "faceted_brep"},
        {"calculation", "isolated_exact_triangle_bvh"},
        {"distance", std::sqrt(std::max(0.0, measured.distanceSquared))},
        {"closest_point_pairs",
         Json::array({{{"first", pointJson(measured.first)}, {"second", pointJson(measured.second)}}})},
        {"first_shape", {{"triangles", first.triangles.size()}}},
        {"second_shape", {{"triangles", second.triangles.size()}}},
    };
}

void writeJson(const std::filesystem::path& path, const Json& payload)
{
    const std::filesystem::path temporary = path.string() + ".tmp";
    std::ofstream output(temporary, std::ios::trunc);
    if (!output) {
        throw std::runtime_error("Cannot open geometry result path: " + path.string());
    }
    output << payload.dump(2) << '\n';
    output.close();
    std::filesystem::rename(temporary, path);
}

}  // namespace

int main(int argc, char** argv)
{
    if (argc != 2) {
        return 2;
    }
    std::filesystem::path resultPath;
    const auto started = Clock::now();
    try {
        std::ifstream input(argv[1]);
        if (!input) {
            throw std::runtime_error("Cannot open geometry request file.");
        }
        Json request;
        input >> request;
        if (request.value("schema", "") != "vibecad-geometry-job-v1") {
            throw std::runtime_error("Unsupported geometry request schema.");
        }
        resultPath = request.at("result_path").get<std::string>();
        const std::string operation = request.value("operation", "");
        Json result;
        if (operation == "validate_brep") {
            if (request.at("shape").at("format").get<std::string>() != "brep") {
                throw std::runtime_error("Shape validation requires a BREP artifact.");
            }
            result = validateBrep(request);
        }
        else if (operation == "inspect_brep") {
            if (request.at("shape").at("format").get<std::string>() != "brep") {
                throw std::runtime_error("Shape inspection requires a BREP artifact.");
            }
            result = inspectBrep(request);
        }
        else if (operation == "minimum_distance") {
            const std::string firstFormat = request.at("first").at("format").get<std::string>();
            const std::string secondFormat = request.at("second").at("format").get<std::string>();
            if (firstFormat == "brep" && secondFormat == "brep") {
                result = brepMinimumDistance(request);
            }
            else if (firstFormat == "stl" && secondFormat == "stl") {
                result = stlMinimumDistance(request);
            }
            else {
                throw std::runtime_error(
                    "Geometry artifacts must both be BREP or both be STL for one distance job."
                );
            }
        }
        else {
            throw std::runtime_error("Unsupported geometry worker operation.");
        }
        result["schema"] = "vibecad-geometry-result-v1";
        result["elapsed_ms"]
            = std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - started).count();
        writeJson(resultPath, result);
        return 0;
    }
    catch (const Standard_Failure& error) {
        if (!resultPath.empty()) {
            writeJson(
                resultPath,
                {{"schema", "vibecad-geometry-result-v1"},
                 {"ok", false},
                 {"failure_stage", "native_call"},
                 {"exception_type", "Standard_Failure"},
                 {"error", error.GetMessageString() ? error.GetMessageString() : "OpenCascade failure"}}
            );
        }
        return 1;
    }
    catch (const std::exception& error) {
        if (!resultPath.empty()) {
            writeJson(
                resultPath,
                {{"schema", "vibecad-geometry-result-v1"},
                 {"ok", false},
                 {"failure_stage", "native_call"},
                 {"exception_type", "std::exception"},
                 {"error", error.what()}}
            );
        }
        return 1;
    }
}
