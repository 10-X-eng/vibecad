/***************************************************************************
 *   Copyright (c) 2019 WandererFan <wandererfan@gmail.com>                *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public           *
 *   License as published by the Free Software Foundation; either          *
 *   version 2 of the License, or (at your option) any later version.      *
 *                                                                         *
 *   This library  is distributed in the hope that it will be useful,      *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
 *   GNU Library General Public License for more details.                  *
 *                                                                         *
 *   You should have received a copy of the GNU Library General Public     *
 *   License along with this library; see the file COPYING.LIB. If not,    *
 *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
 *   Suite 330, Boston, MA  02111-1307, USA                                *
 *                                                                         *
 ***************************************************************************/


# include <BRepBuilderAPI_MakeVertex.hxx>
# include <BRep_Builder.hxx>
# include <BRepBndLib.hxx>
# include <BRepGProp.hxx>
# include <BRep_Tool.hxx>
# include <Bnd_Box.hxx>
# include <GProp_GProps.hxx>
# include <Precision.hxx>
# include <TopExp_Explorer.hxx>
# include <TopExp.hxx>
# include <TopTools_IndexedMapOfShape.hxx>
# include <gp_Pnt.hxx>
# include <TopoDS.hxx>
# include <TopoDS_Compound.hxx>
# include <TopoDS_Edge.hxx>
# include <TopoDS_Shape.hxx>
# include <TopoDS_Vertex.hxx>


#include <Base/Console.h>
#include <Base/Vector3D.h>
#include <Base/VectorPy.h>

#include <Mod/Part/App/TopoShape.h>
#include <Mod/Part/App/TopoShapePy.h>
#include <Mod/Part/App/TopoShapeEdgePy.h>
#include <Mod/Part/App/TopoShapeVertexPy.h>

#include "CenterLine.h"
#include "DrawViewPart.h"
#include "DrawProjectSplit.h"
#include "Geometry.h"
#include "GeometryObject.h"
#include "Cosmetic.h"
#include "DrawUtil.h"
#include "ShapeExtractor.h"

// inclusion of the generated files (generated out of DrawViewPartPy.xml)
#include <Mod/TechDraw/App/CosmeticVertexPy.h>
#include <Mod/TechDraw/App/DrawViewPartPy.h>
#include <Mod/TechDraw/App/DrawViewPartPy.cpp>


using namespace TechDraw;
using DU = DrawUtil;

namespace {

const char* edgeClassName(EdgeClass edgeClass)
{
    switch (edgeClass) {
        case EdgeClass::UVISO:
            return "iso";
        case EdgeClass::OUTLINE:
            return "outline";
        case EdgeClass::SMOOTH:
            return "smooth";
        case EdgeClass::SEAM:
            return "seam";
        case EdgeClass::HARD:
            return "hard";
        case EdgeClass::NONE:
            return "none";
    }
    return "unknown";
}

Py::Dict vectorDescriptor(const Base::Vector3d& value)
{
    Py::Dict result;
    result.setItem("x", Py::Float(value.x));
    result.setItem("y", Py::Float(value.y));
    return result;
}

Py::Dict edgeBounds(const TopoDS_Edge& edge)
{
    Bnd_Box box;
    box.SetGap(0.0);
    BRepBndLib::AddOptimal(edge, box);
    Standard_Real xmin = 0.0;
    Standard_Real ymin = 0.0;
    Standard_Real zmin = 0.0;
    Standard_Real xmax = 0.0;
    Standard_Real ymax = 0.0;
    Standard_Real zmax = 0.0;
    box.Get(xmin, ymin, zmin, xmax, ymax, zmax);
    Py::Dict result;
    result.setItem("min_x", Py::Float(xmin));
    result.setItem("min_y", Py::Float(ymin));
    result.setItem("max_x", Py::Float(xmax));
    result.setItem("max_y", Py::Float(ymax));
    result.setItem("width", Py::Float(xmax - xmin));
    result.setItem("height", Py::Float(ymax - ymin));
    return result;
}

struct ProjectedSourceEdge
{
    std::string objectName;
    std::string subelement;
    TopoDS_Edge edge;
};

struct ProjectedSourceVertex
{
    std::string objectName;
    std::string subelement;
    Base::Vector3d point;
};

struct ProjectedSources
{
    std::vector<ProjectedSourceEdge> edges;
    std::vector<ProjectedSourceVertex> vertices;
};

ProjectedSources projectSourceSubelements(DrawViewPart* view)
{
    ProjectedSources result;
    const Base::Vector3d centroid = view->getOriginalCentroid();
    const gp_Ax2 projectionCS = view->getProjectionCS();

    for (App::DocumentObject* source : view->getAllSources()) {
        if (!source || !source->getNameInDocument()) {
            continue;
        }
        TopoDS_Shape transformed = ShapeExtractor::getLocatedShape(source);
        if (transformed.IsNull()) {
            continue;
        }
        DrawViewPart::centerScaleRotate(view, transformed, centroid);
        const std::string objectName = source->getNameInDocument();

        TopTools_IndexedMapOfShape sourceEdges;
        TopExp::MapShapes(transformed, TopAbs_EDGE, sourceEdges);
        for (int edgeIndex = 1; edgeIndex <= sourceEdges.Extent(); ++edgeIndex) {
            const TopoDS_Edge sourceEdge = TopoDS::Edge(sourceEdges(edgeIndex));
            try {
                const TopoDS_Shape projected = GeometryObject::projectSimpleShape(
                    sourceEdge, projectionCS, true);
                for (TopExp_Explorer projectedEdges(projected, TopAbs_EDGE);
                     projectedEdges.More(); projectedEdges.Next()) {
                    result.edges.push_back({objectName,
                                            "Edge" + std::to_string(edgeIndex),
                                            TopoDS::Edge(projectedEdges.Current())});
                }
            }
            catch (...) {
                // The descriptor reports an unmapped element instead of
                // inventing a source when OCCT cannot project this edge.
            }
        }

        TopTools_IndexedMapOfShape sourceVertices;
        TopExp::MapShapes(transformed, TopAbs_VERTEX, sourceVertices);
        for (int vertexIndex = 1; vertexIndex <= sourceVertices.Extent(); ++vertexIndex) {
            const gp_Pnt point = BRep_Tool::Pnt(
                TopoDS::Vertex(sourceVertices(vertexIndex)));
            const Base::Vector3d projected = view->projectPoint(
                Base::Vector3d(point.X(), point.Y(), point.Z()), true);
            result.vertices.push_back(
                {objectName, "Vertex" + std::to_string(vertexIndex), projected});
        }
    }
    return result;
}

Py::Dict sourceMappingForEdge(const TopoDS_Edge& edge,
                              EdgeClass edgeClass,
                              const ProjectedSources& sources)
{
    Py::List candidates;
    for (const auto& source : sources.edges) {
        if (!DrawProjectSplit::boxesIntersect(source.edge, edge)) {
            continue;
        }
        // A result of 1 means the projected view edge is wholly contained in
        // the projection of this source edge. This handles HLR edge splitting
        // without claiming a mapping for merely crossing curves.
        if (DrawProjectSplit::isSubset(source.edge, edge) != 1) {
            continue;
        }
        Py::Dict candidate;
        candidate.setItem("object_name", Py::String(source.objectName));
        candidate.setItem("subelement", Py::String(source.subelement));
        candidates.append(candidate);
    }

    Py::Dict result;
    result.setItem("candidates", candidates);
    if (candidates.size() == 1) {
        result.setItem("status", Py::String("exact"));
    }
    else if (candidates.size() > 1) {
        result.setItem("status", Py::String("ambiguous"));
    }
    else if (edgeClass == EdgeClass::OUTLINE || edgeClass == EdgeClass::SMOOTH) {
        result.setItem("status", Py::String("generated_projection"));
    }
    else {
        result.setItem("status", Py::String("unmapped"));
    }
    return result;
}

Py::Dict sourceMappingForVertex(const Base::Vector3d& point,
                                bool isCenter,
                                const ProjectedSources& sources)
{
    Py::List candidates;
    const double tolerance = std::max(Precision::Confusion(), 1.0e-6);
    for (const auto& source : sources.vertices) {
        if (!point.IsEqual(source.point, tolerance)) {
            continue;
        }
        Py::Dict candidate;
        candidate.setItem("object_name", Py::String(source.objectName));
        candidate.setItem("subelement", Py::String(source.subelement));
        candidates.append(candidate);
    }
    Py::Dict result;
    result.setItem("candidates", candidates);
    if (candidates.size() == 1) {
        result.setItem("status", Py::String("exact"));
    }
    else if (candidates.size() > 1) {
        result.setItem("status", Py::String("ambiguous"));
    }
    else if (isCenter) {
        result.setItem("status", Py::String("generated_center"));
    }
    else {
        result.setItem("status", Py::String("unmapped"));
    }
    return result;
}

}  // namespace

// returns a string which represents the object e.g. when printed in python
std::string DrawViewPartPy::representation() const
{
    return {"<DrawViewPart object>"};
}
//TODO: gets & sets for geometry

PyObject* DrawViewPartPy::getVisibleEdges(PyObject *args)
{
    //NOLINTNEXTLINE
    PyObject* conventionalCoords = Py_False;    // false for gui display (+Y down), true for calculations (+Y up)
    if (!PyArg_ParseTuple(args, "|O!", &PyBool_Type, &conventionalCoords)) {
        throw Py::ValueError("Expected '[conventionalCoords=True/False] or None' ");
    }

    DrawViewPart* dvp = getDrawViewPartPtr();
    Py::List pEdgeList;
    std::vector<TechDraw::BaseGeomPtr> geoms = dvp->getEdgeGeometry();
    for (auto& g: geoms) {
        if (g->getHlrVisible()) {
            TopoDS_Edge occEdge = g->getOCCEdge();
            if (PyBool_Check(conventionalCoords) && conventionalCoords == Py_True) {
                TopoDS_Shape occShape = ShapeUtils::invertGeometry(occEdge);
                occEdge = TopoDS::Edge(occShape);
            }
            PyObject* pEdge = new Part::TopoShapeEdgePy(new Part::TopoShape(occEdge));
            pEdgeList.append(Py::asObject(pEdge));
        }
    }

    return Py::new_reference_to(pEdgeList);
}

PyObject* DrawViewPartPy::getHiddenEdges(PyObject *args)
{
    PyObject* conventionalCoords = Py_False;    // false for gui display (+Y down), true for calculations (+Y up)
    //NOLINTNEXTLINE
    if (!PyArg_ParseTuple(args, "|O!", &PyBool_Type, &conventionalCoords)) {
        throw Py::ValueError("Expected '[conventionalCoords=True/False] or None' ");
    }

    DrawViewPart* dvp = getDrawViewPartPtr();
    Py::List pEdgeList;
    std::vector<TechDraw::BaseGeomPtr> geoms = dvp->getEdgeGeometry();
    for (auto& g: geoms) {
        if (!g->getHlrVisible()) {
            TopoDS_Edge occEdge = g->getOCCEdge();
            if (PyBool_Check(conventionalCoords) && conventionalCoords == Py_True) {
                TopoDS_Shape occShape = ShapeUtils::invertGeometry(occEdge);
                occEdge = TopoDS::Edge(occShape);
            }
            PyObject* pEdge = new Part::TopoShapeEdgePy(new Part::TopoShape(occEdge));
            pEdgeList.append(Py::asObject(pEdge));
        }
    }

    return Py::new_reference_to(pEdgeList);
}

PyObject* DrawViewPartPy::getVisibleVertexes(PyObject *args)
{
    PyObject* conventionalCoords = Py_False;    // false for gui display (+Y down), true for calculations (+Y up)
    //NOLINTNEXTLINE
    if (!PyArg_ParseTuple(args, "|O!", &PyBool_Type, &conventionalCoords)) {
        throw Py::ValueError("Expected '[conventionalCoords=True/False] or None' ");
    }

    DrawViewPart* dvp = getDrawViewPartPtr();
    Py::List pVertexList;
    auto vertsAll = dvp->getVertexGeometry();
    for (auto& vert: vertsAll) {
        if (vert->getHlrVisible()) {
            Base::Vector3d vertPoint = vert->point();
            if (PyBool_Check(conventionalCoords) && conventionalCoords == Py_True) {
                vertPoint = DU::invertY(vertPoint);
            }
            PyObject* pVertex = new Base::VectorPy(new Base::Vector3d(vertPoint));
            pVertexList.append(Py::asObject(pVertex));
        }
    }

    return Py::new_reference_to(pVertexList);
}

PyObject* DrawViewPartPy::getProjectedElementDescriptors(PyObject* args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }

    DrawViewPart* view = getDrawViewPartPtr();
    if (!view->hasGeometry()) {
        throw Py::RuntimeError("The TechDraw view has no projected geometry.");
    }

    const ProjectedSources projectedSources = projectSourceSubelements(view);
    Py::List edgesOut;
    const auto edges = view->getEdgeGeometry();
    for (size_t index = 0; index < edges.size(); ++index) {
        const auto& geometry = edges.at(index);
        const TopoDS_Edge edge = geometry->getOCCEdge();
        GProp_GProps properties;
        BRepGProp::LinearProperties(edge, properties);

        Py::Dict descriptor;
        descriptor.setItem("name", Py::String("Edge" + std::to_string(index)));
        descriptor.setItem("element_type", Py::String("edge"));
        descriptor.setItem("geometry_type", Py::String(geometry->geomTypeName()));
        descriptor.setItem("edge_class", Py::String(edgeClassName(geometry->getClassOfEdge())));
        descriptor.setItem("visible", Py::Boolean(geometry->getHlrVisible()));
        descriptor.setItem("closed", Py::Boolean(geometry->closed()));
        descriptor.setItem("length_view_mm", Py::Float(properties.Mass()));
        descriptor.setItem("bounds_2d", edgeBounds(edge));
        descriptor.setItem("start_2d", vectorDescriptor(geometry->getStartPoint()));
        descriptor.setItem("end_2d", vectorDescriptor(geometry->getEndPoint()));
        descriptor.setItem("midpoint_2d", vectorDescriptor(geometry->getMidPoint()));
        descriptor.setItem("hlr_source_index", Py::Long(geometry->sourceIndex()));
        descriptor.setItem(
            "source_mapping",
            sourceMappingForEdge(edge, geometry->getClassOfEdge(), projectedSources));

        if (auto circle = std::dynamic_pointer_cast<TechDraw::Circle>(geometry)) {
            descriptor.setItem("center_2d", vectorDescriptor(circle->center));
            descriptor.setItem("radius_view_mm", Py::Float(circle->radius));
        }
        edgesOut.append(descriptor);
    }

    Py::List verticesOut;
    const auto vertices = view->getVertexGeometry();
    for (size_t index = 0; index < vertices.size(); ++index) {
        const auto& vertex = vertices.at(index);
        const Base::Vector3d point = vertex->point();
        Py::Dict descriptor;
        descriptor.setItem("name", Py::String("Vertex" + std::to_string(index)));
        descriptor.setItem("element_type", Py::String("vertex"));
        descriptor.setItem("point_2d", vectorDescriptor(point));
        descriptor.setItem("visible", Py::Boolean(vertex->getHlrVisible()));
        descriptor.setItem("is_center", Py::Boolean(vertex->isCenter()));
        descriptor.setItem("is_reference", Py::Boolean(vertex->isReference()));
        descriptor.setItem(
            "source_mapping",
            sourceMappingForVertex(point, vertex->isCenter(), projectedSources));
        verticesOut.append(descriptor);
    }

    Py::Dict result;
    result.setItem("coordinate_space", Py::String("view_projection_scaled_centered"));
    result.setItem("view_scale", Py::Float(view->getScale()));
    result.setItem("edges", edgesOut);
    result.setItem("vertices", verticesOut);
    return Py::new_reference_to(result);
}

PyObject* DrawViewPartPy::getPrecomputedProjection(PyObject* args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }

    DrawViewPart* view = getDrawViewPartPtr();
    if (!view->hasGeometry()) {
        throw Py::RuntimeError("The TechDraw view has no projected geometry.");
    }

    BRep_Builder builder;
    TopoDS_Compound edges;
    TopoDS_Compound faces;
    builder.MakeCompound(edges);
    builder.MakeCompound(faces);

    Py::List edgeClasses;
    Py::List edgeVisibility;
    Py::List sourceIndices;
    for (const auto& edge : view->getEdgeGeometry()) {
        builder.Add(edges, edge->getOCCEdge());
        edgeClasses.append(Py::Long(static_cast<long>(edge->getClassOfEdge())));
        edgeVisibility.append(Py::Boolean(edge->getHlrVisible()));
        sourceIndices.append(Py::Long(edge->sourceIndex()));
    }
    for (const auto& face : view->getFaceGeometry()) {
        const TopoDS_Face occFace = face->toOccFace();
        if (!occFace.IsNull()) {
            builder.Add(faces, occFace);
        }
    }

    Py::Dict result;
    result.setItem(
        "edges",
        Py::asObject(new Part::TopoShapePy(new Part::TopoShape(edges))));
    result.setItem(
        "faces",
        Py::asObject(new Part::TopoShapePy(new Part::TopoShape(faces))));
    result.setItem("edge_classes", edgeClasses);
    result.setItem("edge_visibility", edgeVisibility);
    result.setItem("source_indices", sourceIndices);
    result.setItem(
        "centroid",
        Py::asObject(new Base::VectorPy(new Base::Vector3d(view->getOriginalCentroid()))));
    return Py::new_reference_to(result);
}

PyObject* DrawViewPartPy::setPrecomputedProjection(PyObject* args)
{
    PyObject* snapshot = nullptr;
    if (!PyArg_ParseTuple(args, "O!", &PyDict_Type, &snapshot)) {
        return nullptr;
    }
    if (PyDict_Size(snapshot) != 6) {
        throw Py::ValueError(
            "Projection snapshot must contain exactly edges, faces, edge_classes, "
            "edge_visibility, source_indices, and centroid.");
    }

    PyObject* edgesObject = PyDict_GetItemString(snapshot, "edges");
    PyObject* facesObject = PyDict_GetItemString(snapshot, "faces");
    PyObject* classesObject = PyDict_GetItemString(snapshot, "edge_classes");
    PyObject* visibilityObject = PyDict_GetItemString(snapshot, "edge_visibility");
    PyObject* sourcesObject = PyDict_GetItemString(snapshot, "source_indices");
    PyObject* centroidObject = PyDict_GetItemString(snapshot, "centroid");
    if (!edgesObject || !facesObject || !classesObject || !visibilityObject
        || !sourcesObject || !centroidObject) {
        throw Py::ValueError("Projection snapshot is missing a required field.");
    }
    if (!PyObject_TypeCheck(edgesObject, &Part::TopoShapePy::Type)
        || !PyObject_TypeCheck(facesObject, &Part::TopoShapePy::Type)) {
        throw Py::TypeError("Projection snapshot edges and faces must be Part shapes.");
    }
    if (!PyObject_TypeCheck(centroidObject, &Base::VectorPy::Type)) {
        throw Py::TypeError("Projection snapshot centroid must be an App.Vector.");
    }

    auto sequenceLongs = [](PyObject* value, const char* field) {
        PyObject* sequence = PySequence_Fast(value, "Projection metadata must be a sequence.");
        if (!sequence) {
            throw Py::TypeError(std::string("Projection snapshot ") + field
                                + " must be a sequence.");
        }
        Py::Object sequenceOwner(sequence, true);
        std::vector<long> result;
        const Py_ssize_t size = PySequence_Fast_GET_SIZE(sequence);
        result.reserve(static_cast<size_t>(size));
        PyObject** items = PySequence_Fast_ITEMS(sequence);
        for (Py_ssize_t index = 0; index < size; ++index) {
            if (!PyLong_Check(items[index]) || PyBool_Check(items[index])) {
                throw Py::TypeError(std::string("Projection snapshot ") + field
                                    + " must contain only integers.");
            }
            const long item = PyLong_AsLong(items[index]);
            if (PyErr_Occurred()) {
                throw Py::OverflowError(std::string("Projection snapshot ") + field
                                        + " contains an out-of-range integer.");
            }
            result.push_back(item);
        }
        return result;
    };
    auto sequenceBools = [](PyObject* value, const char* field) {
        PyObject* sequence = PySequence_Fast(value, "Projection metadata must be a sequence.");
        if (!sequence) {
            throw Py::TypeError(std::string("Projection snapshot ") + field
                                + " must be a sequence.");
        }
        Py::Object sequenceOwner(sequence, true);
        std::vector<bool> result;
        const Py_ssize_t size = PySequence_Fast_GET_SIZE(sequence);
        result.reserve(static_cast<size_t>(size));
        PyObject** items = PySequence_Fast_ITEMS(sequence);
        for (Py_ssize_t index = 0; index < size; ++index) {
            if (!PyBool_Check(items[index])) {
                throw Py::TypeError(std::string("Projection snapshot ") + field
                                    + " must contain only bools.");
            }
            result.push_back(items[index] == Py_True);
        }
        return result;
    };

    const TopoDS_Shape edges =
        static_cast<Part::TopoShapePy*>(edgesObject)->getTopoShapePtr()->getShape();
    const TopoDS_Shape faces =
        static_cast<Part::TopoShapePy*>(facesObject)->getTopoShapePtr()->getShape();
    const Base::Vector3d centroid =
        static_cast<Base::VectorPy*>(centroidObject)->value();
    getDrawViewPartPtr()->setPrecomputedProjection(
        edges,
        sequenceLongs(classesObject, "edge_classes"),
        sequenceBools(visibilityObject, "edge_visibility"),
        sequenceLongs(sourcesObject, "source_indices"),
        faces,
        centroid);
    Py_Return;
}

PyObject* DrawViewPartPy::getHiddenVertexes(PyObject *args)
{
    PyObject* conventionalCoords = Py_False;    // false for gui display (+Y down), true for calculations (+Y up)
    //NOLINTNEXTLINE
    if (!PyArg_ParseTuple(args, "|O!", &PyBool_Type, &conventionalCoords)) {
        throw Py::ValueError("Expected '[conventionalCoords=True/False] or None' ");
    }

    DrawViewPart* dvp = getDrawViewPartPtr();
    Py::List pVertexList;
    auto vertsAll = dvp->getVertexGeometry();
    for (auto& vert: vertsAll) {
        if (!vert->getHlrVisible()) {
            Base::Vector3d vertPoint = vert->point();
            if (PyBool_Check(conventionalCoords) && conventionalCoords == Py_True) {
                vertPoint = DU::invertY(vertPoint);
            }
            PyObject* pVertex = new Base::VectorPy(new Base::Vector3d(vertPoint));
            pVertexList.append(Py::asObject(pVertex));
        }
    }

    return Py::new_reference_to(pVertexList);
}


PyObject* DrawViewPartPy::requestPaint(PyObject *args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }

    DrawViewPart* item = getDrawViewPartPtr();
    item->requestPaint();

    Py_Return;
}

PyObject* DrawViewPartPy::getGeometricCenter(PyObject *args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }

    DrawViewPart* dvp = getDrawViewPartPtr();
    Base::Vector3d pointOut = dvp->getCurrentCentroid();
    return new Base::VectorPy(new Base::Vector3d(pointOut));
}


// remove all cosmetics
PyObject* DrawViewPartPy::clearCosmeticVertices(PyObject *args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }

    DrawViewPart* item = getDrawViewPartPtr();
    item->clearCosmeticVertexes();

    Py_Return;
}

PyObject* DrawViewPartPy::clearCosmeticEdges(PyObject *args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }

    DrawViewPart* item = getDrawViewPartPtr();
    item->clearCosmeticEdges();

    Py_Return;
}

PyObject* DrawViewPartPy::clearCenterLines(PyObject *args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }

    DrawViewPart* item = getDrawViewPartPtr();
    item->clearCenterLines();

    Py_Return;
}

PyObject* DrawViewPartPy::clearGeomFormats(PyObject *args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }

    DrawViewPart* item = getDrawViewPartPtr();
    item->clearGeomFormats();

    Py_Return;
}

//********* Cosmetic Vertex Routines *******************************************
PyObject* DrawViewPartPy::makeCosmeticVertex(PyObject *args)
{
    PyObject* pPnt1 = nullptr;
    if (!PyArg_ParseTuple(args, "O!", &(Base::VectorPy::Type), &pPnt1)) {
        return nullptr;
    }

    DrawViewPart* dvp = getDrawViewPartPtr();
    if (!dvp->hasGeometry()) {
        Base::Console().error("%s has no geometry yet. Can not add cosmetic vertex.\n", dvp->Label.getValue());
        Py_Return;
    }
    Base::Vector3d pnt1 = static_cast<Base::VectorPy*>(pPnt1)->value();
    std::string id = dvp->addCosmeticVertex(pnt1);
    dvp->add1CVToGV(id);
    dvp->requestPaint();

    return PyUnicode_FromString(id.c_str());   //return tag for new CV
}

//! make a cosmetic vertex from a 3d point
PyObject* DrawViewPartPy::makeCosmeticVertex3d(PyObject *args)
{
    PyObject* pPnt1 = nullptr;
    if (!PyArg_ParseTuple(args, "O!", &(Base::VectorPy::Type), &pPnt1)) {
        return nullptr;
    }

    DrawViewPart* dvp = getDrawViewPartPtr();
    if (!dvp->hasGeometry()) {
        Base::Console().error("%s has no geometry yet. Can not add cosmetic vertex.\n", dvp->Label.getValue());
        Py_Return;
    }
    Base::Vector3d pnt1 = static_cast<Base::VectorPy*>(pPnt1)->value();
    Base::Vector3d centroid = dvp->getOriginalCentroid();
    // center the point
    pnt1 = pnt1 - centroid;
    // project but do not invert
    Base::Vector3d projected = dvp->projectPoint(pnt1);
    // this is a real world point, it is not scaled or rotated, so so it is in canonical form
    // add and invert the point.
    std::string id = dvp->addCosmeticVertex(projected);
    //int link =
    dvp->add1CVToGV(id);
    dvp->refreshCVGeoms();
    dvp->requestPaint();

    return PyUnicode_FromString(id.c_str());   //return tag for new CV
}

//get by unique tag
PyObject* DrawViewPartPy::getCosmeticVertex(PyObject *args)
{
    const char* id{};                      //unique tag
    if (!PyArg_ParseTuple(args, "s", &id)) {
        return nullptr;
    }

    DrawViewPart* dvp = getDrawViewPartPtr();
    TechDraw::CosmeticVertex* cv = dvp->getCosmeticVertex(id);
    if (cv) {
        return cv->getPyObject();
    }

    Py_Return;
}

//get by selection name
PyObject* DrawViewPartPy::getCosmeticVertexBySelection(PyObject *args)
{
    const char* selName;           //Selection routine name - "Vertex0"
    if (!PyArg_ParseTuple(args, "s", &selName)) {
        return nullptr;
    }

    DrawViewPart* dvp = getDrawViewPartPtr();
    TechDraw::CosmeticVertex* cv = dvp->getCosmeticVertexBySelection(selName);
    if (cv) {
        return cv->getPyObject();
    }

    Py_Return;
}

PyObject* DrawViewPartPy::removeCosmeticVertex(PyObject *args)
{
    DrawViewPart* dvp = getDrawViewPartPtr();
    const char* tag{};
    if (PyArg_ParseTuple(args, "s", &tag)) {
        dvp->removeCosmeticVertex(tag);
        dvp->refreshCVGeoms();
        dvp->requestPaint();
        Py_Return;
    }

    PyErr_Clear();
    PyObject* pCVToDelete = nullptr;
    if (PyArg_ParseTuple(args, "O!", &(TechDraw::CosmeticVertexPy::Type), &pCVToDelete)) {
        auto* cvPy = static_cast<TechDraw::CosmeticVertexPy*>(pCVToDelete);
        TechDraw::CosmeticVertex* cv = cvPy->getCosmeticVertexPtr();
        dvp->removeCosmeticVertex(cv->getTagAsString());
        dvp->refreshCVGeoms();
        dvp->requestPaint();
        Py_Return;
    }

    PyErr_Clear();
    PyObject* pDelList = nullptr;
    if (!PyArg_ParseTuple(args, "O", &pDelList)) {
        return nullptr;
    }

    if (PySequence_Check(pDelList))  {
        Py::Sequence sequence(pDelList);
        for (const auto& item : sequence) {
            if (!PyObject_TypeCheck(item.ptr(), &(TechDraw::CosmeticVertexPy::Type)))  {
                PyErr_Format(PyExc_TypeError ,"Types in sequence must be 'CosmeticVertex', not %s",
                    Py_TYPE(item.ptr())->tp_name);
                return nullptr;
            }
            auto* cvPy = static_cast<TechDraw::CosmeticVertexPy*>(item.ptr());
            TechDraw::CosmeticVertex* cv = cvPy->getCosmeticVertexPtr();
            dvp->removeCosmeticVertex(cv->getTagAsString());
        }
        dvp->refreshCVGeoms();
        dvp->requestPaint();

        Py_Return;
    }

    PyErr_SetString(PyExc_TypeError, "Expected string, CosmeticVertex or sequence of CosmeticVertex");
    return nullptr;
}


//********* Cosmetic Line Routines *********************************************

PyObject* DrawViewPartPy::makeCosmeticLine(PyObject *args)
{
    // the input points are expected to use conventional coordinates (Y up) and need to be inverted
    // before building the line
    PyObject* pPnt1 = nullptr;
    PyObject* pPnt2 = nullptr;
    int style = LineFormat::getDefEdgeStyle();
    double weight = LineFormat::getDefEdgeWidth();
    Base::Color defCol = LineFormat::getDefEdgeColor();
    PyObject* pColor = nullptr;

    if (!PyArg_ParseTuple(args, "O!O!|idO!", &(Base::VectorPy::Type), &pPnt1,
                                        &(Base::VectorPy::Type), &pPnt2,
                                        &style, &weight,
                                        &PyTuple_Type, &pColor)) {
        return nullptr;
    }

    DrawViewPart* dvp = getDrawViewPartPtr();
    if (!dvp->hasGeometry()) {
        Base::Console().error("%s has no geometry yet. Can not add cosmetic line.\n", dvp->Label.getValue());
        Py_Return;
    }

    Base::Vector3d pnt1 = static_cast<Base::VectorPy*>(pPnt1)->value();
    Base::Vector3d pnt2 = static_cast<Base::VectorPy*>(pPnt2)->value();
    std::string newTag = dvp->addCosmeticEdge(DU::invertY(pnt1), DU::invertY(pnt2));
    TechDraw::CosmeticEdge* ce = dvp->getCosmeticEdge(newTag);
    if (ce) {
        ce->m_format.setStyle(style);
        ce->m_format.setWidth(weight);
        ce->m_format.setColor(pColor ? DrawUtil::pyTupleToColor(pColor) : defCol);
    }
    else {
        PyErr_SetString(PyExc_RuntimeError, "DVPPI:makeCosmeticLine - line creation failed");
        return nullptr;
    }
    //int link =
    dvp->add1CEToGE(newTag);
    dvp->requestPaint();

    return PyUnicode_FromString(newTag.c_str());   //return tag for new CE
}

PyObject* DrawViewPartPy::makeCosmeticLine3D(PyObject *args)
{
    // input points are expected to be conventional 3d points
    PyObject* pPnt1 = nullptr;
    PyObject* pPnt2 = nullptr;
    int style = LineFormat::getDefEdgeStyle();
    double weight = LineFormat::getDefEdgeWidth();
    Base::Color defCol = LineFormat::getDefEdgeColor();
    PyObject* pColor = nullptr;

    if (!PyArg_ParseTuple(args, "O!O!|idO!", &(Base::VectorPy::Type), &pPnt1,
                                        &(Base::VectorPy::Type), &pPnt2,
                                        &style, &weight,
                                        &PyTuple_Type, &pColor)) {
        return nullptr;
    }

    DrawViewPart* dvp = getDrawViewPartPtr();
    if (!dvp->hasGeometry()) {
        Base::Console().error("%s has no geometry yet. Can not add cosmetic line.\n", dvp->Label.getValue());
        Py_Return;
    }
    Base::Vector3d centroid = dvp->getOriginalCentroid();

    Base::Vector3d pnt1 = static_cast<Base::VectorPy*>(pPnt1)->value();
    pnt1 = pnt1 - centroid;
    pnt1 = dvp->projectPoint(pnt1);

    Base::Vector3d pnt2 = static_cast<Base::VectorPy*>(pPnt2)->value();
    pnt2 = pnt2 - centroid;
    pnt2 = dvp->projectPoint(pnt2);

    std::string newTag = dvp->addCosmeticEdge(pnt1, pnt2);
    TechDraw::CosmeticEdge* ce = dvp->getCosmeticEdge(newTag);
    if (ce) {
        ce->m_format.setStyle(style);
        ce->m_format.setWidth(weight);
        ce->m_format.setColor(pColor ? DrawUtil::pyTupleToColor(pColor) : defCol);
    }
    else {
        PyErr_SetString(PyExc_RuntimeError, "DVPPI:makeCosmeticLine - line creation failed");
        return nullptr;
    }
    //int link =
    dvp->add1CEToGE(newTag);
    dvp->requestPaint();

    return PyUnicode_FromString(newTag.c_str());   //return tag for new CE
}

PyObject* DrawViewPartPy::makeCosmeticCircle(PyObject *args)
{
    PyObject* pPnt1 = nullptr;
    constexpr double DefaultRadius{5.0};
    double radius = DefaultRadius;
    int style = LineFormat::getDefEdgeStyle();
    double weight = LineFormat::getDefEdgeWidth();
    Base::Color defCol = LineFormat::getDefEdgeColor();
    PyObject* pColor = nullptr;

    if (!PyArg_ParseTuple(args, "O!d|idO!", &(Base::VectorPy::Type), &pPnt1,
                                        &radius,
                                        &style, &weight,
                                        &PyTuple_Type, &pColor)) {
        return nullptr;
    }

    DrawViewPart* dvp = getDrawViewPartPtr();
    if (!dvp->hasGeometry()) {
        Base::Console().error("%s has no geometry yet. Can not add cosmetic circle.\n", dvp->Label.getValue());
        Py_Return;
    }

    Base::Vector3d pnt1 = static_cast<Base::VectorPy*>(pPnt1)->value();
    TechDraw::BaseGeomPtr bg = std::make_shared<TechDraw::Circle> (pnt1, radius);
    std::string newTag = dvp->addCosmeticEdge(bg->inverted());
    TechDraw::CosmeticEdge* ce = dvp->getCosmeticEdge(newTag);
    if (ce) {
        ce->permaRadius = radius;
        ce->m_format.setStyle(style);
        ce->m_format.setWidth(weight);
        ce->m_format.setColor(pColor ? DrawUtil::pyTupleToColor(pColor) : defCol);
    }
    else {
        PyErr_SetString(PyExc_RuntimeError, "DVPPI:makeCosmeticCircle - circle creation failed");
        return nullptr;
    }
    //int link =
    dvp->add1CEToGE(newTag);
    dvp->requestPaint();

    return PyUnicode_FromString(newTag.c_str());   //return tag for new CE
}

PyObject* DrawViewPartPy::makeCosmeticCircleArc(PyObject *args)
{
    PyObject* pPnt1 = nullptr;
    constexpr double DefaultRadius{5.0};
    constexpr double DegreesInCircle{360.0};
    double radius = DefaultRadius;
    double angle1 = 0.0;
    double angle2 = DegreesInCircle;
    int style = LineFormat::getDefEdgeStyle();
    double weight = LineFormat::getDefEdgeWidth();
    Base::Color defCol = LineFormat::getDefEdgeColor();
    PyObject* pColor = nullptr;

    if (!PyArg_ParseTuple(args, "O!ddd|idO!", &(Base::VectorPy::Type), &pPnt1,
                                        &radius, &angle1, &angle2,
                                        &style, &weight, &PyTuple_Type, &pColor)) {
        return nullptr;
    }

    //from here on is almost duplicate of makeCosmeticCircle
    DrawViewPart* dvp = getDrawViewPartPtr();
    if (!dvp->hasGeometry()) {
        Base::Console().error("%s has no geometry yet. Can not add cosmetic circle arc.\n", dvp->Label.getValue());
        Py_Return;
    }

    Base::Vector3d pnt1 = static_cast<Base::VectorPy*>(pPnt1)->value();
    TechDraw::BaseGeomPtr bg = std::make_shared<TechDraw::AOC> (pnt1, radius, angle1, angle2);
    std::string newTag = dvp->addCosmeticEdge(bg->inverted());
    TechDraw::CosmeticEdge* ce = dvp->getCosmeticEdge(newTag);
    if (ce) {
        ce->permaRadius = radius;
        ce->m_format.setStyle(style);
        ce->m_format.setWidth(weight);
        if (!pColor){
            ce->m_format.setColor(defCol);
        }
        else {
            ce->m_format.setColor(DrawUtil::pyTupleToColor(pColor));
        }
    }
    else {
        PyErr_SetString(PyExc_RuntimeError, "DVPPI:makeCosmeticCircleArc - arc creation failed");
        return nullptr;
    }

    //int link =
    dvp->add1CEToGE(newTag);
    dvp->requestPaint();

    return PyUnicode_FromString(newTag.c_str());   //return tag for new CE
}

PyObject* DrawViewPartPy::makeCosmeticCircle3d(PyObject *args)
{
    PyObject* pPnt1 = nullptr;
    constexpr double DefaultRadius{5.0};
    double radius = DefaultRadius;
    int style = LineFormat::getDefEdgeStyle();
    double weight = LineFormat::getDefEdgeWidth();
    Base::Color defCol = LineFormat::getDefEdgeColor();
    PyObject* pColor = nullptr;

    if (!PyArg_ParseTuple(args, "O!d|idO!", &(Base::VectorPy::Type), &pPnt1,
                                        &radius,
                                        &style, &weight,
                                        &PyTuple_Type, &pColor)) {
        return nullptr;
    }

    DrawViewPart* dvp = getDrawViewPartPtr();
    if (!dvp->hasGeometry()) {
        Base::Console().error("%s has no geometry yet. Can not add cosmetic circle.\n", dvp->Label.getValue());
        Py_Return;
    }

    Base::Vector3d pnt1 = static_cast<Base::VectorPy*>(pPnt1)->value();
    // center, project and invert the 3d point
    Base::Vector3d centroid = dvp->getOriginalCentroid();
    pnt1 = DrawUtil::invertY(dvp->projectPoint(pnt1 - centroid));
    TechDraw::BaseGeomPtr bg = std::make_shared<TechDraw::Circle> (pnt1, radius);
    std::string newTag = dvp->addCosmeticEdge(bg->inverted());
    TechDraw::CosmeticEdge* ce = dvp->getCosmeticEdge(newTag);
    if (ce) {
        ce->permaRadius = radius;
        ce->m_format.setStyle(style);
        ce->m_format.setWidth(weight);
        ce->m_format.setColor(pColor ? DrawUtil::pyTupleToColor(pColor) : defCol);
    }
    else {
        PyErr_SetString(PyExc_RuntimeError, "DVPPI:makeCosmeticCircle - circle creation failed");
        return nullptr;
    }
    //int link =
    dvp->add1CEToGE(newTag);
    dvp->requestPaint();

    return PyUnicode_FromString(newTag.c_str());   //return tag for new CE
}

PyObject* DrawViewPartPy::makeCosmeticCircleArc3d(PyObject *args)
{
    PyObject* pPnt1 = nullptr;
    constexpr double DefaultRadius{5.0};
    double radius = DefaultRadius;
    double angle1 = 0.0;
    constexpr double DegreesInCircle{360.0};
    double angle2 = DegreesInCircle;
    int style = LineFormat::getDefEdgeStyle();
    double weight = LineFormat::getDefEdgeWidth();
    Base::Color defCol = LineFormat::getDefEdgeColor();
    PyObject* pColor = nullptr;

    if (!PyArg_ParseTuple(args, "O!ddd|idO!", &(Base::VectorPy::Type), &pPnt1,
                                        &radius, &angle1, &angle2,
                                        &style, &weight, &PyTuple_Type, &pColor)) {
        return nullptr;
    }

    //from here on is almost duplicate of makeCosmeticCircle
    DrawViewPart* dvp = getDrawViewPartPtr();
    if (!dvp->hasGeometry()) {
        Base::Console().error("%s has no geometry yet. Can not add cosmetic circle arc.\n", dvp->Label.getValue());
        Py_Return;
    }

    Base::Vector3d pnt1 = static_cast<Base::VectorPy*>(pPnt1)->value();
    // center, project and invert the 3d point
    Base::Vector3d centroid = dvp->getOriginalCentroid();
    pnt1 = DrawUtil::invertY(dvp->projectPoint(pnt1 - centroid));
    TechDraw::BaseGeomPtr bg = std::make_shared<TechDraw::AOC> (pnt1, radius, angle1, angle2);
    std::string newTag = dvp->addCosmeticEdge(bg->inverted());
    TechDraw::CosmeticEdge* ce = dvp->getCosmeticEdge(newTag);
    if (ce) {
        ce->permaRadius = radius;
        ce->m_format.setStyle(style);
        ce->m_format.setWidth(weight);
        if (!pColor) {
            ce->m_format.setColor(defCol);
        }
        else {
            ce->m_format.setColor(DrawUtil::pyTupleToColor(pColor));
        }
    }
    else {
        PyErr_SetString(PyExc_RuntimeError, "DVPPI:makeCosmeticCircleArc - arc creation failed");
        return nullptr;
    }

    //int link =
    dvp->add1CEToGE(newTag);
    dvp->requestPaint();

    return PyUnicode_FromString(newTag.c_str());   //return tag for new CE
}

//********** Cosmetic Edge *****************************************************

PyObject* DrawViewPartPy::getCosmeticEdge(PyObject *args)
{
    char* tag{};
    if (!PyArg_ParseTuple(args, "s", &tag)) {
        return nullptr;
    }

    DrawViewPart* dvp = getDrawViewPartPtr();
    TechDraw::CosmeticEdge* ce = dvp->getCosmeticEdge(tag);
    if (ce) {
        return ce->getPyObject();
    }

    PyErr_Format(PyExc_ValueError, "DVPPI::getCosmeticEdge - edge %s not found", tag);
    return nullptr;
}

PyObject* DrawViewPartPy::getCosmeticEdgeBySelection(PyObject *args)
{
    char* name{};
    if (!PyArg_ParseTuple(args, "s", &name)) {
        return nullptr;
    }

    DrawViewPart* dvp = getDrawViewPartPtr();

    TechDraw::CosmeticEdge* ce = dvp->getCosmeticEdgeBySelection(name);
    if (ce) {
        return ce->getPyObject();
    }

    PyErr_Format(PyExc_ValueError, "DVPPI::getCosmeticEdgebySelection - edge for name %s not found", name);
    return nullptr;
}

PyObject* DrawViewPartPy::removeCosmeticEdge(PyObject *args)
{
    char* tag{};
    if (!PyArg_ParseTuple(args, "s", &tag)) {
        return nullptr;
    }

    DrawViewPart* dvp = getDrawViewPartPtr();
    dvp->removeCosmeticEdge(tag);

    Py_Return;
}

//********** Center Line *******************************************************

PyObject* DrawViewPartPy::makeCenterLine(PyObject *args)
{
    PyObject* pSubs{};
    CenterLine::Mode mode = CenterLine::Mode::VERTICAL;
    std::vector<std::string> subs;

    if (!PyArg_ParseTuple(args, "O!i", &PyList_Type, &pSubs, &mode)) {
        return nullptr;
    }

    DrawViewPart* dvp = getDrawViewPartPtr();
    if (!dvp->hasGeometry()) {
        Base::Console().error("%s has no geometry yet. Can not add center line.\n", dvp->Label.getValue());
        Py_Return;
    }

    int size = PyList_Size(pSubs);
    int i = 0;
    for ( ; i < size; i++) {
        PyObject* po = PyList_GetItem(pSubs, i);
        if (PyUnicode_Check(po)) {
            std::string s = PyUnicode_AsUTF8(po);
            subs.push_back(s);
        }
        else {
            PyErr_SetString(PyExc_TypeError, "Expected list of string");
            return nullptr;
        }
    }

    CenterLine* cl = nullptr;
    std::string tag;
    if (!subs.empty()) {
        cl = CenterLine::CenterLineBuilder(dvp, subs, mode);     //vert, horiz, align
        if (cl) {
            tag = dvp->addCenterLine(cl);
        }
        else {
            PyErr_SetString(PyExc_RuntimeError, "DVPPI:makeCenterLine - line creation failed");
            return nullptr;
        }
    }
    //int link =
    dvp->add1CLToGE(tag);
    dvp->requestPaint();

    return PyUnicode_FromString(tag.c_str());   //return tag for new CV
}

PyObject* DrawViewPartPy::getCenterLine(PyObject *args)
{
    char* tag{};
    if (!PyArg_ParseTuple(args, "s", &tag)) {
        return nullptr;
    }

    DrawViewPart* dvp = getDrawViewPartPtr();
    TechDraw::CenterLine* cl = dvp->getCenterLine(tag);
    if (cl) {
        return  cl->getPyObject();
    }
    PyErr_Format(PyExc_ValueError, "DVPPI::getCenterLine - centerLine %s not found", tag);
    return nullptr;
}

PyObject* DrawViewPartPy::getCenterLineBySelection(PyObject *args)
{
    char* tag{};
    if (!PyArg_ParseTuple(args, "s", &tag)) {
        return nullptr;
    }

    DrawViewPart* dvp = getDrawViewPartPtr();
    TechDraw::CenterLine* cl = dvp->getCenterLineBySelection(tag);
    if (cl) {
        return cl->getPyObject();
    }
    PyErr_Format(PyExc_ValueError, "DVPPI::getCenterLinebySelection - centerLine for tag %s not found", tag);
    return nullptr;
}

PyObject* DrawViewPartPy::removeCenterLine(PyObject *args)
{
    char* tag{};
    if (!PyArg_ParseTuple(args, "s", &tag)) {
        return nullptr;
    }

    DrawViewPart* dvp = getDrawViewPartPtr();
    dvp->removeCenterLine(tag);

    Py_Return;
}

//********** Geometry Edge *****************************************************

PyObject* DrawViewPartPy::formatGeometricEdge(PyObject *args)
{
    int idx = -1;
    int style = Qt::SolidLine;
    Base::Color color = LineFormat::getDefEdgeColor();
    constexpr double DefaultWeight{0.5};
    double weight = DefaultWeight;
    int visible = 1;
    PyObject* pColor{};

    if (!PyArg_ParseTuple(args, "iidOi", &idx, &style, &weight, &pColor, &visible)) {
        return nullptr;
    }

    color = DrawUtil::pyTupleToColor(pColor);
    DrawViewPart* dvp = getDrawViewPartPtr();
    TechDraw::GeomFormat* gf = dvp->getGeomFormatBySelection(idx);
    if (gf) {
        gf->m_format.setStyle(style);
        gf->m_format.setColor(color);
        gf->m_format.setWidth(weight);
        gf->m_format.setVisible(visible);
    }
    else {
        TechDraw::LineFormat fmt(style, weight, color, visible);
        auto* newGF = new TechDraw::GeomFormat(idx, fmt);
//                    int idx =
        dvp->addGeomFormat(newGF);
    }

    Py_Return;
}

//------------------------------------------------------------------------------
PyObject* DrawViewPartPy::getEdgeByIndex(PyObject *args)
{
    int edgeIndex = 0;
    if (!PyArg_ParseTuple(args, "i", &edgeIndex)) {
        return nullptr;
    }

    DrawViewPart* dvp = getDrawViewPartPtr();

    //this is scaled and +Yup
    //need unscaled and +Ydown
    TechDraw::BaseGeomPtr geom = dvp->getGeomByIndex(edgeIndex);
    if (!geom) {
        PyErr_SetString(PyExc_ValueError, "Wrong edge index");
        return nullptr;
    }

    TopoDS_Shape temp = ShapeUtils::mirrorShapeVec(geom->getOCCEdge(),
                                      Base::Vector3d(0.0, 0.0, 0.0),
                                      1.0 / dvp->getScale());

    TopoDS_Edge outEdge = TopoDS::Edge(temp);

    return new Part::TopoShapeEdgePy(new Part::TopoShape(outEdge));
}

PyObject* DrawViewPartPy::getVertexByIndex(PyObject *args)
{
    int vertexIndex = 0;
    if (!PyArg_ParseTuple(args, "i", &vertexIndex)) {
        return nullptr;
    }

    DrawViewPart* dvp = getDrawViewPartPtr();

    //this is scaled and +Yup
    //need unscaled and +Ydown
    TechDraw::VertexPtr vert = dvp->getProjVertexByIndex(vertexIndex);
    if (!vert) {
        PyErr_SetString(PyExc_ValueError, "Wrong vertex index");
        return nullptr;
    }

    Base::Vector3d point = DrawUtil::invertY(vert->point()) / dvp->getScale();

    gp_Pnt gPoint(point.x, point.y, point.z);
    BRepBuilderAPI_MakeVertex mkVertex(gPoint);
    TopoDS_Vertex outVertex = mkVertex.Vertex();

    return new Part::TopoShapeVertexPy(new Part::TopoShape(outVertex));
}

PyObject* DrawViewPartPy::getEdgeBySelection(PyObject *args)
{
    int edgeIndex = 0;
    char* selName{};           //Selection routine name - "Edge0"
    if (!PyArg_ParseTuple(args, "s", &selName)) {
        return nullptr;
    }

    edgeIndex = DrawUtil::getIndexFromName(std::string(selName));
    DrawViewPart* dvp = getDrawViewPartPtr();

    //this is scaled and +Yup
    //need unscaled and +Ydown
    TechDraw::BaseGeomPtr geom = dvp->getGeomByIndex(edgeIndex);
    if (!geom) {
        PyErr_SetString(PyExc_ValueError, "Wrong edge index");
        return nullptr;
    }

    TopoDS_Shape temp = ShapeUtils::mirrorShapeVec(geom->getOCCEdge(),
                                      Base::Vector3d(0.0, 0.0, 0.0),
                                      1.0 / dvp->getScale());

    TopoDS_Edge outEdge = TopoDS::Edge(temp);

    return new Part::TopoShapeEdgePy(new Part::TopoShape(outEdge));
}

PyObject* DrawViewPartPy::getVertexBySelection(PyObject *args)
{
    int vertexIndex = 0;
    const char* selName{};           //Selection routine name - "Vertex0"
    if (!PyArg_ParseTuple(args, "s", &selName)) {
        return nullptr;
    }

    vertexIndex = DrawUtil::getIndexFromName(std::string(selName));
    DrawViewPart* dvp = getDrawViewPartPtr();

    //this is scaled and +Yup
    //need unscaled and +Ydown
    TechDraw::VertexPtr vert = dvp->getProjVertexByIndex(vertexIndex);
    if (!vert) {
        PyErr_SetString(PyExc_ValueError, "Wrong vertex index");
        return nullptr;
    }

    Base::Vector3d point = DrawUtil::invertY(vert->point()) / dvp->getScale();
    gp_Pnt gPoint(point.x, point.y, point.z);
    BRepBuilderAPI_MakeVertex mkVertex(gPoint);
    TopoDS_Vertex outVertex = mkVertex.Vertex();

    return new Part::TopoShapeVertexPy(new Part::TopoShape(outVertex));
}

PyObject* DrawViewPartPy::projectPoint(PyObject *args)
{
    PyObject* pPoint = nullptr;
    PyObject* pInvert = Py_False;
    if (!PyArg_ParseTuple(args, "O!|O!", &(Base::VectorPy::Type), &pPoint, &PyBool_Type, &pInvert)) {
        return nullptr;
    }

    bool invert = Base::asBoolean(pInvert);

    DrawViewPart* dvp = getDrawViewPartPtr();
    Base::Vector3d projection = dvp->projectPoint(static_cast<Base::VectorPy*>(pPoint)->value(), invert);

    return new Base::VectorPy(new Base::Vector3d(projection));
}

//==============================================================================
PyObject *DrawViewPartPy::getCustomAttributes(const char* /*attr*/) const
{
    return nullptr;
}

int DrawViewPartPy::setCustomAttributes(const char* /*attr*/, PyObject* /*obj*/)
{
    return 0;
}
