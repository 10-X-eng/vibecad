/***************************************************************************
 *   Copyright (c) 2026 VibeCAD contributors                              *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public           *
 *   License as published by the Free Software Foundation; either          *
 *   version 2 of the License, or (at your option) any later version.      *
 ***************************************************************************/

#include <Base/VectorPy.h>

#include <Mod/Part/App/TopoShape.h>
#include <Mod/Part/App/TopoShapePy.h>

#include "DrawComplexSection.h"

#include <Mod/TechDraw/App/DrawViewSectionPy.h>
#include <Mod/TechDraw/App/DrawComplexSectionPy.h>
#include <Mod/TechDraw/App/DrawComplexSectionPy.cpp>


using namespace TechDraw;


std::string DrawComplexSectionPy::representation() const
{
    return std::string("<DrawComplexSection object>");
}

PyObject* DrawComplexSectionPy::getPrecomputedComplexSection(PyObject* args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }

    const auto snapshot = getDrawComplexSectionPtr()->getPrecomputedComplexSection();
    Py::Dict result;
    result.setItem(
        "cut_pieces",
        Py::asObject(new Part::TopoShapePy(new Part::TopoShape(snapshot.cutPieces))));
    result.setItem(
        "section_faces",
        Py::asObject(new Part::TopoShapePy(new Part::TopoShape(snapshot.sectionFaces))));
    result.setItem(
        "prepared_shape",
        Py::asObject(new Part::TopoShapePy(new Part::TopoShape(snapshot.preparedShape))));
    result.setItem(
        "centroid",
        Py::asObject(new Base::VectorPy(new Base::Vector3d(snapshot.centroid))));
    return Py::new_reference_to(result);
}

PyObject* DrawComplexSectionPy::setPrecomputedComplexSection(PyObject* args)
{
    PyObject* snapshot = nullptr;
    if (!PyArg_ParseTuple(args, "O!", &PyDict_Type, &snapshot)) {
        return nullptr;
    }
    if (PyDict_Size(snapshot) != 4) {
        throw Py::ValueError(
            "Complex-section snapshot must contain exactly cut_pieces, section_faces, "
            "prepared_shape, and centroid.");
    }

    PyObject* cutObject = PyDict_GetItemString(snapshot, "cut_pieces");
    PyObject* facesObject = PyDict_GetItemString(snapshot, "section_faces");
    PyObject* preparedObject = PyDict_GetItemString(snapshot, "prepared_shape");
    PyObject* centroidObject = PyDict_GetItemString(snapshot, "centroid");
    if (!cutObject || !facesObject || !preparedObject || !centroidObject) {
        throw Py::ValueError("Complex-section snapshot is missing a required field.");
    }
    if (!PyObject_TypeCheck(cutObject, &Part::TopoShapePy::Type)
        || !PyObject_TypeCheck(facesObject, &Part::TopoShapePy::Type)
        || !PyObject_TypeCheck(preparedObject, &Part::TopoShapePy::Type)) {
        throw Py::TypeError(
            "Complex-section cut_pieces, section_faces, and prepared_shape must be Part shapes.");
    }
    if (!PyObject_TypeCheck(centroidObject, &Base::VectorPy::Type)) {
        throw Py::TypeError("Complex-section centroid must be an App.Vector.");
    }

    const auto shape = [](PyObject* object) {
        return static_cast<Part::TopoShapePy*>(object)->getTopoShapePtr()->getShape();
    };
    getDrawComplexSectionPtr()->setPrecomputedComplexSection(
        shape(cutObject),
        shape(facesObject),
        shape(preparedObject),
        static_cast<Base::VectorPy*>(centroidObject)->value());
    Py_Return;
}

PyObject* DrawComplexSectionPy::getCustomAttributes(const char* /*attr*/) const
{
    return nullptr;
}

int DrawComplexSectionPy::setCustomAttributes(const char* /*attr*/, PyObject* /*obj*/)
{
    return 0;
}
