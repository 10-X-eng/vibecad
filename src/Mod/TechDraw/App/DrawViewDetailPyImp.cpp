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

#include <Mod/Part/App/TopoShape.h>
#include <Mod/Part/App/TopoShapePy.h>

#include "DrawViewDetail.h"

#include <Mod/TechDraw/App/DrawViewPartPy.h>
#include <Mod/TechDraw/App/DrawViewDetailPy.h>
#include <Mod/TechDraw/App/DrawViewDetailPy.cpp>


using namespace TechDraw;


std::string DrawViewDetailPy::representation() const
{
    return std::string("<DrawViewDetail object>");
}

PyObject* DrawViewDetailPy::getPrecomputedDetail(PyObject* args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }
    const auto snapshot = getDrawViewDetailPtr()->getPrecomputedDetail();
    Py::Dict result;
    result.setItem(
        "detail_shape",
        Py::asObject(new Part::TopoShapePy(new Part::TopoShape(snapshot.detailShape))));
    return Py::new_reference_to(result);
}

PyObject* DrawViewDetailPy::setPrecomputedDetail(PyObject* args)
{
    PyObject* snapshot = nullptr;
    if (!PyArg_ParseTuple(args, "O!", &PyDict_Type, &snapshot)) {
        return nullptr;
    }
    if (PyDict_Size(snapshot) != 1) {
        throw Py::ValueError(
            "Detail snapshot must contain exactly detail_shape.");
    }
    PyObject* shapeObject = PyDict_GetItemString(snapshot, "detail_shape");
    if (!shapeObject) {
        throw Py::ValueError("Detail snapshot is missing detail_shape.");
    }
    if (!PyObject_TypeCheck(shapeObject, &Part::TopoShapePy::Type)) {
        throw Py::TypeError("Detail snapshot detail_shape must be a Part shape.");
    }
    const TopoDS_Shape shape =
        static_cast<Part::TopoShapePy*>(shapeObject)->getTopoShapePtr()->getShape();
    getDrawViewDetailPtr()->setPrecomputedDetail(shape);
    Py_Return;
}

PyObject* DrawViewDetailPy::requestPrecomputedDetailPaint(PyObject* args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }
    getDrawViewDetailPtr()->requestPrecomputedDetailPaint();
    Py_Return;
}

PyObject* DrawViewDetailPy::getCustomAttributes(const char* /*attr*/) const
{
    return nullptr;
}

int DrawViewDetailPy::setCustomAttributes(const char* /*attr*/, PyObject* /*obj*/)
{
    return 0;
}
