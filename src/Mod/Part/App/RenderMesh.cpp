// SPDX-License-Identifier: LGPL-2.1-or-later

#include "RenderMesh.h"

#include <memory>
#include <stdexcept>

#include <BRepBuilderAPI_Copy.hxx>
#include <BRepMesh_IncrementalMesh.hxx>
#include <BRepTools.hxx>
#include <BRep_Tool.hxx>
#include <IMeshTools_Parameters.hxx>
#include <Poly_Array1OfTriangle.hxx>
#include <Poly_Polygon3D.hxx>
#include <Poly_PolygonOnTriangulation.hxx>
#include <Poly_Triangulation.hxx>
#include <Precision.hxx>
#include <Standard_Version.hxx>
#include <TColStd_Array1OfInteger.hxx>
#include <TColgp_Array1OfDir.hxx>
#include <TColgp_Array1OfPnt.hxx>
#include <TopExp.hxx>
#include <TopExp_Explorer.hxx>
#include <TopTools_IndexedMapOfShape.hxx>
#include <TopTools_MapOfShape.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Edge.hxx>
#include <TopoDS_Face.hxx>
#include <TopoDS_Shape.hxx>
#include <TopoDS_Vertex.hxx>
#include <gp_Trsf.hxx>

#include <Base/Converter.h>
#include <Base/Tools.h>
#include <App/Application.h>
#include <App/HostRuntime.h>

#include "Tools.h"
#include "ProgressIndicator.h"

namespace
{

constexpr std::int32_t endPrimitive = -1;

void checkCancellation(const std::stop_token& stopToken)
{
    if (stopToken.stop_requested()) {
        throw std::runtime_error("Render mesh preparation cancelled");
    }
}

void setVector(std::vector<float>& values, int index, const Base::Vector3f& value)
{
    const std::size_t offset = static_cast<std::size_t>(index) * 3;
    values[offset] = value.x;
    values[offset + 1] = value.y;
    values[offset + 2] = value.z;
}

void addVector(std::vector<float>& values, int index, const Base::Vector3f& value)
{
    const std::size_t offset = static_cast<std::size_t>(index) * 3;
    values[offset] += value.x;
    values[offset + 1] += value.y;
    values[offset + 2] += value.z;
}

}  // namespace

Part::RenderMesh Part::prepareRenderMesh(
    const TopoDS_Shape& sourceShape,
    double deviation,
    double angularDeflection,
    bool normalsFromUV,
    std::stop_token stopToken
)
{
    RenderMesh result;
    checkCancellation(stopToken);
    if (Tools::isShapeEmpty(sourceShape)) {
        return result;
    }

    checkCancellation(stopToken);
    TopoDS_Shape shape = BRepBuilderAPI_Copy(
                             sourceShape,
                             Standard_True,
                             Standard_False
    ).Shape();
    if (Tools::isShapeEmpty(shape)) {
        return result;
    }

    // The view provider applies the object's Placement in the scene graph.
    // Mesh in that object's local coordinates, retaining child locations in
    // compounds. Otherwise both vertices and normals receive the root transform
    // twice. Only the private copy changes; live/source geometry is untouched.
    shape.Location(TopLoc_Location());

    Standard_Real deflection = Tools::getDeflection(shape, deviation);
    if (deflection < gp::Resolution()) {
        deflection = Precision::Confusion();
    }

    IMeshTools_Parameters meshParameters;
    meshParameters.Deflection = deflection;
    meshParameters.Relative = Standard_False;
    meshParameters.Angle = Base::toRadians(angularDeflection);
    // VibeCAD fans independent shapes across its bounded persistent pool. An
    // inner OCCT pool here would oversubscribe that application-wide budget.
    meshParameters.InParallel = Standard_False;
    meshParameters.AllowQualityDecrease = Standard_True;

#if OCC_VERSION_HEX < 0x070600
    BRepTools::Clean(shape);
#else
    BRepTools::Clean(shape, Standard_True);
#endif
    checkCancellation(stopToken);
    // The owner already reports this render operation. Give OCCT the same
    // cancellation token so an obsolete mesh can stop inside tessellation,
    // rather than occupying its worker until the entire kernel call returns.
    Handle(Part::ProgressIndicator) progress = new Part::ProgressIndicator(stopToken);
    BRepMesh_IncrementalMesh(shape, meshParameters, progress->Start());
    checkCancellation(stopToken);

    int triangleCount = 0;
    int nodeCount = 0;
    int normalCount = 0;
    TopTools_MapOfShape faceEdges;
    TopTools_IndexedMapOfShape faceMap;
    TopExp::MapShapes(shape, TopAbs_FACE, faceMap);
    TopTools_IndexedMapOfShape solidMap;
    TopExp::MapShapes(shape, TopAbs_SOLID, solidMap);
    result.solidFaceIndices.reserve(solidMap.Extent());
    for (int solidIndex = 1; solidIndex <= solidMap.Extent(); ++solidIndex) {
        checkCancellation(stopToken);
        TopTools_IndexedMapOfShape solidFaces;
        TopExp::MapShapes(solidMap(solidIndex), TopAbs_FACE, solidFaces);
        auto& indices = result.solidFaceIndices.emplace_back();
        indices.reserve(solidFaces.Extent());
        for (int i = 1; i <= solidFaces.Extent(); ++i) {
            checkCancellation(stopToken);
            const int faceIndex = faceMap.FindIndex(solidFaces(i));
            if (faceIndex == 0) {
                throw std::logic_error("Solid face missing from render mesh topology");
            }
            indices.push_back(faceIndex - 1);
        }
    }

    TopTools_IndexedMapOfShape edgeMap;
    TopExp::MapShapes(shape, TopAbs_EDGE, edgeMap);
    struct FaceInput
    {
        Handle(Poly_Triangulation) mesh;
        TopLoc_Location location;
        int nodeOffset {};
        int triangleOffset {};
        std::unique_ptr<TColgp_Array1OfDir> uvNormals;
        std::vector<std::pair<int, Handle(Poly_PolygonOnTriangulation)>> edges;
    };
    std::vector<FaceInput> faces(faceMap.Extent());
    std::vector<bool> edgeAssigned(edgeMap.Extent() + 1, false);
    for (int faceIndex = 1; faceIndex <= faceMap.Extent(); ++faceIndex) {
        checkCancellation(stopToken);
        auto& input = faces[faceIndex - 1];
        input.nodeOffset = nodeCount;
        input.triangleOffset = triangleCount;
        const TopoDS_Face& face = TopoDS::Face(faceMap(faceIndex));
        auto& mesh = input.mesh;
        mesh = BRep_Tool::Triangulation(face, input.location);
        if (mesh.IsNull()) {
            mesh = Tools::triangulationOfFace(face);
        }
        if (!mesh.IsNull()) {
            triangleCount += mesh->NbTriangles();
            nodeCount += mesh->NbNodes();
            normalCount += mesh->NbNodes();
            if (normalsFromUV) {
                // This helper populates the triangulation's normal cache.
                // Finish those mutations before parallel readers use meshes
                // shared by located instances of the same face.
                input.uvNormals = std::make_unique<TColgp_Array1OfDir>(1, mesh->NbNodes());
                Tools::getPointNormals(face, mesh, *input.uvNormals);
            }
        }
        for (TopExp_Explorer explorer(face, TopAbs_EDGE); explorer.More(); explorer.Next()) {
            const auto& edge = TopoDS::Edge(explorer.Current());
            faceEdges.Add(edge);
            const int edgeIndex = edgeMap.FindIndex(edge);
            if (!mesh.IsNull() && !edgeAssigned[edgeIndex]) {
                auto polygon = BRep_Tool::PolygonOnTriangulation(edge, mesh, input.location);
                if (!polygon.IsNull()) {
                    input.edges.emplace_back(edgeIndex, std::move(polygon));
                    edgeAssigned[edgeIndex] = true;
                }
            }
        }
    }

    // Each indexed edge has one writer, selected above. Pre-size the outer
    // storage so independent face workers never mutate a shared container.
    std::vector<std::vector<std::int32_t>> lineIndicesByEdge(edgeMap.Extent() + 1);
    for (int edgeIndex = 1; edgeIndex <= edgeMap.Extent(); ++edgeIndex) {
        checkCancellation(stopToken);
        const TopoDS_Edge& edge = TopoDS::Edge(edgeMap(edgeIndex));
        TopLoc_Location edgeLocation;
        if (!faceEdges.Contains(edge)) {
            const Handle(Poly_Polygon3D) polygon = Tools::polygonOfEdge(edge, edgeLocation);
            if (!polygon.IsNull()) {
                nodeCount += polygon->NbNodes();
            }
        }
    }

    TopTools_IndexedMapOfShape vertexMap;
    TopExp::MapShapes(shape, TopAbs_VERTEX, vertexMap);
    nodeCount += vertexMap.Extent();

    result.vertices.resize(static_cast<std::size_t>(nodeCount) * 3);
    result.normals.resize(static_cast<std::size_t>(normalCount) * 3);
    result.triangleIndices.resize(static_cast<std::size_t>(triangleCount) * 4);
    result.faceTriangleCounts.resize(faceMap.Extent());

    App::GetApplication().hostRuntime().parallelFor(faces.size(), [&](std::size_t index) {
        checkCancellation(stopToken);
        const int faceIndex = static_cast<int>(index) + 1;
        const auto& input = faces[index];
        const auto& faceLocation = input.location;
        const auto& mesh = input.mesh;
        const int faceNodeOffset = input.nodeOffset;
        const int faceTriangleOffset = input.triangleOffset;
        const TopoDS_Face& face = TopoDS::Face(faceMap(faceIndex));
        if (mesh.IsNull()) {
            result.faceTriangleCounts[faceIndex - 1] = 0;
            return;
        }

        const int nodesInFace = mesh->NbNodes();
        const int trianglesInFace = mesh->NbTriangles();
        const TopAbs_Orientation orientation = face.Orientation();
        const bool identity = faceLocation.IsIdentity();
        const gp_Trsf transformation = faceLocation.Transformation();
#if OCC_VERSION_HEX < 0x070600
        const Poly_Array1OfTriangle& triangles = mesh->Triangles();
        const TColgp_Array1OfPnt& nodes = mesh->Nodes();
#endif

        for (int triangleIndex = 1; triangleIndex <= trianglesInFace; ++triangleIndex) {
            if ((triangleIndex & 255) == 0) {
                checkCancellation(stopToken);
            }
            Standard_Integer first;
            Standard_Integer second;
            Standard_Integer third;
#if OCC_VERSION_HEX < 0x070600
            triangles(triangleIndex).Get(first, second, third);
            gp_Pnt firstPoint(nodes(first));
            gp_Pnt secondPoint(nodes(second));
            gp_Pnt thirdPoint(nodes(third));
#else
            mesh->Triangle(triangleIndex).Get(first, second, third);
            gp_Pnt firstPoint(mesh->Node(first));
            gp_Pnt secondPoint(mesh->Node(second));
            gp_Pnt thirdPoint(mesh->Node(third));
#endif
            if (orientation != TopAbs_FORWARD) {
                std::swap(first, second);
                std::swap(firstPoint, secondPoint);
            }

            gp_Vec firstNormal;
            gp_Vec secondNormal;
            gp_Vec thirdNormal;
            if (normalsFromUV) {
                const auto& faceNormals = *input.uvNormals;
                firstNormal.SetXYZ(faceNormals(first).XYZ());
                secondNormal.SetXYZ(faceNormals(second).XYZ());
                thirdNormal.SetXYZ(faceNormals(third).XYZ());
            }
            else {
                const gp_Vec edgeOne(firstPoint, secondPoint);
                const gp_Vec edgeTwo(firstPoint, thirdPoint);
                const gp_Vec normal = edgeOne ^ edgeTwo;
                firstNormal = normal;
                secondNormal = normal;
                thirdNormal = normal;
            }

            if (!identity) {
                firstPoint.Transform(transformation);
                secondPoint.Transform(transformation);
                thirdPoint.Transform(transformation);
                firstNormal.Transform(transformation);
                secondNormal.Transform(transformation);
                thirdNormal.Transform(transformation);
            }

            addVector(
                result.normals,
                faceNodeOffset + first - 1,
                Base::convertTo<Base::Vector3f>(firstNormal)
            );
            addVector(
                result.normals,
                faceNodeOffset + second - 1,
                Base::convertTo<Base::Vector3f>(secondNormal)
            );
            addVector(
                result.normals,
                faceNodeOffset + third - 1,
                Base::convertTo<Base::Vector3f>(thirdNormal)
            );
            setVector(
                result.vertices,
                faceNodeOffset + first - 1,
                Base::convertTo<Base::Vector3f>(firstPoint)
            );
            setVector(
                result.vertices,
                faceNodeOffset + second - 1,
                Base::convertTo<Base::Vector3f>(secondPoint)
            );
            setVector(
                result.vertices,
                faceNodeOffset + third - 1,
                Base::convertTo<Base::Vector3f>(thirdPoint)
            );

            const int outputOffset = (faceTriangleOffset + triangleIndex - 1) * 4;
            result.triangleIndices[outputOffset] = faceNodeOffset + first - 1;
            result.triangleIndices[outputOffset + 1] = faceNodeOffset + second - 1;
            result.triangleIndices[outputOffset + 2] = faceNodeOffset + third - 1;
            result.triangleIndices[outputOffset + 3] = endPrimitive;
        }

        result.faceTriangleCounts[faceIndex - 1] = trianglesInFace;
        for (const auto& [edgeIndex, polygon] : input.edges) {
            const TColStd_Array1OfInteger& indices = polygon->Nodes();
            lineIndicesByEdge[edgeIndex].reserve(indices.Length());
            for (Standard_Integer index = indices.Lower(); index <= indices.Upper(); ++index) {
                if ((index & 255) == 0) {
                    checkCancellation(stopToken);
                }
                const int nodeIndex = indices(index);
                const int outputIndex = faceNodeOffset + nodeIndex - 1;
                lineIndicesByEdge[edgeIndex].push_back(outputIndex);
#if OCC_VERSION_HEX < 0x070600
                gp_Pnt point(nodes(nodeIndex));
#else
                gp_Pnt point(mesh->Node(nodeIndex));
#endif
                if (!identity) {
                    point.Transform(transformation);
                }
                setVector(
                    result.vertices,
                    outputIndex,
                    Base::convertTo<Base::Vector3f>(point)
                );
            }
        }

        for (int node = 0; node < nodesInFace; ++node) {
            if ((node & 255) == 0) {
                checkCancellation(stopToken);
            }
            const auto offset = static_cast<std::size_t>(faceNodeOffset + node) * 3;
            Base::Vector3f normal(result.normals[offset], result.normals[offset + 1],
                                  result.normals[offset + 2]);
            normal.Normalize();
            result.normals[offset] = normal.x;
            result.normals[offset + 1] = normal.y;
            result.normals[offset + 2] = normal.z;
        }
    });

    int faceNodeOffset = normalCount;
    for (int edgeIndex = 1; edgeIndex <= edgeMap.Extent(); ++edgeIndex) {
        checkCancellation(stopToken);
        const TopoDS_Edge& edge = TopoDS::Edge(edgeMap(edgeIndex));
        if (faceEdges.Contains(edge)) {
            continue;
        }
        TopLoc_Location edgeLocation;
        const Handle(Poly_Polygon3D) polygon = Tools::polygonOfEdge(edge, edgeLocation);
        if (polygon.IsNull()) {
            continue;
        }
        const bool identity = edgeLocation.IsIdentity();
        const gp_Trsf transformation = edgeLocation.Transformation();
        const TColgp_Array1OfPnt& nodes = polygon->Nodes();
        for (Standard_Integer index = 1; index <= polygon->NbNodes(); ++index) {
            if ((index & 255) == 0) {
                checkCancellation(stopToken);
            }
            gp_Pnt point = nodes(index);
            if (!identity) {
                point.Transform(transformation);
            }
            setVector(
                result.vertices,
                faceNodeOffset,
                Base::convertTo<Base::Vector3f>(point)
            );
            lineIndicesByEdge[edgeIndex].push_back(faceNodeOffset);
            ++faceNodeOffset;
        }
    }

    result.vertexStart = faceNodeOffset;
    for (int vertexIndex = 1; vertexIndex <= vertexMap.Extent(); ++vertexIndex) {
        if ((vertexIndex & 255) == 0) {
            checkCancellation(stopToken);
        }
        const gp_Pnt point = BRep_Tool::Pnt(TopoDS::Vertex(vertexMap(vertexIndex)));
        setVector(
            result.vertices,
            faceNodeOffset + vertexIndex - 1,
            Base::convertTo<Base::Vector3f>(point)
        );
    }

    std::size_t renderedEdges = 0;
    for (const auto& indices : lineIndicesByEdge) {
        checkCancellation(stopToken);
        if (indices.empty()) {
            continue;
        }
        result.lineIndices.insert(result.lineIndices.end(), indices.begin(), indices.end());
        result.lineIndices.push_back(endPrimitive);
        ++renderedEdges;
    }
    result.lineMaterialIndices.assign(renderedEdges + 1, 0);
    checkCancellation(stopToken);
    return result;
}
