/***************************************************************************
 *   Copyright (c) 2026 VibeCAD contributors                              *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public           *
 *   License as published by the Free Software Foundation; either          *
 *   version 2 of the License, or (at your option) any later version.      *
 *                                                                         *
 *   This library is distributed in the hope that it will be useful,       *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU      *
 *   Library General Public License for more details.                      *
 ***************************************************************************/

#include <Base/VectorPy.h>

#include <Mod/Part/App/TopoShape.h>
#include <Mod/Part/App/TopoShapePy.h>

#include "DrawViewSection.h"

// Inclusion of the generated files.
#include <Mod/TechDraw/App/DrawViewPartPy.h>
#include <Mod/TechDraw/App/DrawViewSectionPy.h>
#include <Mod/TechDraw/App/DrawViewSectionPy.cpp>


using namespace TechDraw;


std::string DrawViewSectionPy::representation() const
{
    return std::string("<DrawViewSection object>");
}

PyObject* DrawViewSectionPy::getPrecomputedSection(PyObject* args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }

    const auto snapshot = getDrawViewSectionPtr()->getPrecomputedSection();
    Py::Dict result;
    result.setItem(
        "cut_pieces",
        Py::asObject(new Part::TopoShapePy(new Part::TopoShape(snapshot.cutPieces))));
    result.setItem(
        "section_faces",
        Py::asObject(new Part::TopoShapePy(new Part::TopoShape(snapshot.sectionFaces))));
    result.setItem(
        "centroid",
        Py::asObject(new Base::VectorPy(new Base::Vector3d(snapshot.centroid))));
    return Py::new_reference_to(result);
}

PyObject* DrawViewSectionPy::setPrecomputedSection(PyObject* args)
{
    PyObject* snapshot = nullptr;
    if (!PyArg_ParseTuple(args, "O!", &PyDict_Type, &snapshot)) {
        return nullptr;
    }
    if (PyDict_Size(snapshot) != 3) {
        throw Py::ValueError(
            "Section snapshot must contain exactly cut_pieces, section_faces, and centroid.");
    }

    PyObject* cutObject = PyDict_GetItemString(snapshot, "cut_pieces");
    PyObject* facesObject = PyDict_GetItemString(snapshot, "section_faces");
    PyObject* centroidObject = PyDict_GetItemString(snapshot, "centroid");
    if (!cutObject || !facesObject || !centroidObject) {
        throw Py::ValueError("Section snapshot is missing a required field.");
    }
    if (!PyObject_TypeCheck(cutObject, &Part::TopoShapePy::Type)
        || !PyObject_TypeCheck(facesObject, &Part::TopoShapePy::Type)) {
        throw Py::TypeError("Section snapshot cut_pieces and section_faces must be Part shapes.");
    }
    if (!PyObject_TypeCheck(centroidObject, &Base::VectorPy::Type)) {
        throw Py::TypeError("Section snapshot centroid must be an App.Vector.");
    }

    const TopoDS_Shape cutPieces =
        static_cast<Part::TopoShapePy*>(cutObject)->getTopoShapePtr()->getShape();
    const TopoDS_Shape sectionFaces =
        static_cast<Part::TopoShapePy*>(facesObject)->getTopoShapePtr()->getShape();
    const Base::Vector3d centroid =
        static_cast<Base::VectorPy*>(centroidObject)->value();
    getDrawViewSectionPtr()->setPrecomputedSection(cutPieces, sectionFaces, centroid);
    Py_Return;
}

PyObject* DrawViewSectionPy::requestPrecomputedSectionPaint(PyObject* args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }
    getDrawViewSectionPtr()->requestPrecomputedSectionPaint();
    Py_Return;
}

PyObject* DrawViewSectionPy::getCustomAttributes(const char* /*attr*/) const
{
    return nullptr;
}

int DrawViewSectionPy::setCustomAttributes(const char* /*attr*/, PyObject* /*obj*/)
{
    return 0;
}
