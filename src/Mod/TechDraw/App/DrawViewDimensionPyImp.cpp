/***************************************************************************
 *   Copyright (c) 2018 WandererFan <wandererfan@gmail.com>                *
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


#include <Base/Vector3D.h>
#include <Base/VectorPy.h>

#include "DrawViewDimension.h"
// inclusion of the generated files (generated out of DrawViewDimensionPy.xml)
#include <Mod/TechDraw/App/DrawViewDimensionPy.h>
#include <Mod/TechDraw/App/DrawViewDimensionPy.cpp>


using namespace TechDraw;

// returns a string which represents the object e.g. when printed in python
std::string DrawViewDimensionPy::representation() const
{
    return std::string("<DrawViewDimension object>");
}

PyObject* DrawViewDimensionPy::getRawValue(PyObject* args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }

    DrawViewDimension* dvd = getDrawViewDimensionPtr();
    double val = dvd->getDimValue();
    PyObject* pyVal = PyFloat_FromDouble(val);
    return pyVal;
}

PyObject* DrawViewDimensionPy::getText(PyObject* args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }

    DrawViewDimension* dvd = getDrawViewDimensionPtr();
    std::string  textString = dvd->getFormattedDimensionValue();
//TODO: check multiversion code!
    PyObject* pyText = Base::PyAsUnicodeObject(textString);
    return pyText;
}

PyObject* DrawViewDimensionPy::getPrecomputedDimension(PyObject* args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }

    DrawViewDimension* dimension = getDrawViewDimensionPtr();
    const auto flags = dimension->getPrecomputedDimensionFlags();
    if (flags.empty() || !flags[0] || !flags[3]) {
        throw Py::RuntimeError(
            "The TechDraw dimension has no valid computed descriptive geometry.");
    }

    Py::List vectors;
    for (const Base::Vector3d& vector : dimension->getPrecomputedDimensionVectors()) {
        vectors.append(Py::asObject(new Base::VectorPy(new Base::Vector3d(vector))));
    }
    Py::List scalars;
    for (double scalar : dimension->getPrecomputedDimensionScalars()) {
        scalars.append(Py::Float(scalar));
    }
    Py::List pyFlags;
    for (bool flag : flags) {
        pyFlags.append(Py::Boolean(flag));
    }
    Py::Dict result;
    result.setItem("vectors", vectors);
    result.setItem("scalars", scalars);
    result.setItem("flags", pyFlags);
    return Py::new_reference_to(result);
}

PyObject* DrawViewDimensionPy::setPrecomputedDimension(PyObject* args)
{
    PyObject* snapshot = nullptr;
    if (!PyArg_ParseTuple(args, "O!", &PyDict_Type, &snapshot)) {
        return nullptr;
    }
    if (PyDict_Size(snapshot) != 3) {
        throw Py::ValueError(
            "Dimension snapshot must contain exactly vectors, scalars, and flags.");
    }
    PyObject* vectorsObject = PyDict_GetItemString(snapshot, "vectors");
    PyObject* scalarsObject = PyDict_GetItemString(snapshot, "scalars");
    PyObject* flagsObject = PyDict_GetItemString(snapshot, "flags");
    if (!vectorsObject || !scalarsObject || !flagsObject) {
        throw Py::ValueError("Dimension snapshot is missing a required field.");
    }

    auto sequence = [](PyObject* value, const char* field) {
        PyObject* result = PySequence_Fast(value, "Dimension metadata must be a sequence.");
        if (!result) {
            throw Py::TypeError(std::string("Dimension snapshot ") + field
                                + " must be a sequence.");
        }
        return Py::Object(result, true);
    };

    Py::Object vectorSequence = sequence(vectorsObject, "vectors");
    Py::Object scalarSequence = sequence(scalarsObject, "scalars");
    Py::Object flagSequence = sequence(flagsObject, "flags");

    std::vector<Base::Vector3d> vectors;
    const Py_ssize_t vectorCount = PySequence_Fast_GET_SIZE(vectorSequence.ptr());
    vectors.reserve(static_cast<size_t>(vectorCount));
    PyObject** vectorItems = PySequence_Fast_ITEMS(vectorSequence.ptr());
    for (Py_ssize_t index = 0; index < vectorCount; ++index) {
        if (!PyObject_TypeCheck(vectorItems[index], &Base::VectorPy::Type)) {
            throw Py::TypeError(
                "Dimension snapshot vectors must contain only App.Vector values.");
        }
        vectors.push_back(static_cast<Base::VectorPy*>(vectorItems[index])->value());
    }

    std::vector<double> scalars;
    const Py_ssize_t scalarCount = PySequence_Fast_GET_SIZE(scalarSequence.ptr());
    scalars.reserve(static_cast<size_t>(scalarCount));
    PyObject** scalarItems = PySequence_Fast_ITEMS(scalarSequence.ptr());
    for (Py_ssize_t index = 0; index < scalarCount; ++index) {
        if (PyBool_Check(scalarItems[index])
            || (!PyFloat_Check(scalarItems[index]) && !PyLong_Check(scalarItems[index]))) {
            throw Py::TypeError(
                "Dimension snapshot scalars must contain only finite numbers.");
        }
        const double scalar = PyFloat_AsDouble(scalarItems[index]);
        if (PyErr_Occurred()) {
            throw Py::ValueError("Dimension snapshot contains an invalid scalar.");
        }
        scalars.push_back(scalar);
    }

    std::vector<bool> flags;
    const Py_ssize_t flagCount = PySequence_Fast_GET_SIZE(flagSequence.ptr());
    flags.reserve(static_cast<size_t>(flagCount));
    PyObject** flagItems = PySequence_Fast_ITEMS(flagSequence.ptr());
    for (Py_ssize_t index = 0; index < flagCount; ++index) {
        if (!PyBool_Check(flagItems[index])) {
            throw Py::TypeError(
                "Dimension snapshot flags must contain only bools.");
        }
        flags.push_back(flagItems[index] == Py_True);
    }

    getDrawViewDimensionPtr()->setPrecomputedDimension(vectors, scalars, flags);
    Py_Return;
}

PyObject* DrawViewDimensionPy::getLinearPoints(PyObject* args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }

    DrawViewDimension* dvd = getDrawViewDimensionPtr();
    pointPair pts = dvd->getLinearPoints();
    Py::List ret;
    ret.append(Py::asObject(new Base::VectorPy(new Base::Vector3d(pts.first()))));
    ret.append(Py::asObject(new Base::VectorPy(new Base::Vector3d(pts.second()))));
    return Py::new_reference_to(ret);
}

PyObject* DrawViewDimensionPy::getArcPoints(PyObject* args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }

    DrawViewDimension* dvd = getDrawViewDimensionPtr();
    arcPoints pts = dvd->getArcPoints();
    Py::List ret;
    ret.append(Py::asObject(new Base::VectorPy(new Base::Vector3d(pts.center))));
    ret.append(Py::asObject(new Base::VectorPy(new Base::Vector3d(pts.onCurve.first()))));
    ret.append(Py::asObject(new Base::VectorPy(new Base::Vector3d(pts.onCurve.second()))));
    ret.append(Py::asObject(new Base::VectorPy(new Base::Vector3d(pts.arcEnds.first()))));
    ret.append(Py::asObject(new Base::VectorPy(new Base::Vector3d(pts.arcEnds.second()))));
    ret.append(Py::asObject(new Base::VectorPy(new Base::Vector3d(pts.midArc))));
    return Py::new_reference_to(ret);
}

PyObject* DrawViewDimensionPy::getAnglePoints(PyObject* args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }

    DrawViewDimension* dvd = getDrawViewDimensionPtr();
    anglePoints pts = dvd->getAnglePoints();
    Py::List ret;
    ret.append(Py::asObject(new Base::VectorPy(new Base::Vector3d(pts.first()))));
    ret.append(Py::asObject(new Base::VectorPy(new Base::Vector3d(pts.second()))));
    ret.append(Py::asObject(new Base::VectorPy(new Base::Vector3d(pts.vertex()))));
    return Py::new_reference_to(ret);
}


PyObject* DrawViewDimensionPy::getAreaPoints(PyObject* args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }

    DrawViewDimension* dvd = getDrawViewDimensionPtr();
    areaPoint pts = dvd->getAreaPoint();
    Py::List ret;
    ret.append(Py::asObject(new Base::VectorPy(new Base::Vector3d(pts.getCenter()))));
    ret.append(Py::asObject(PyFloat_FromDouble(pts.getFilledArea())));
    ret.append(Py::asObject(PyFloat_FromDouble(pts.getActualArea())));
    return Py::new_reference_to(ret);
}

PyObject* DrawViewDimensionPy::getArrowPositions(PyObject* args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }

    DrawViewDimension* dvd = getDrawViewDimensionPtr();
    pointPair pts = dvd->getArrowPositions();
    Py::List ret;
    ret.append(Py::asObject(new Base::VectorPy(new Base::Vector3d(pts.first()))));
    ret.append(Py::asObject(new Base::VectorPy(new Base::Vector3d(pts.second()))));
    return Py::new_reference_to(ret);
}
PyObject *DrawViewDimensionPy::getCustomAttributes(const char* /*attr*/) const
{
    return nullptr;
}

int DrawViewDimensionPy::setCustomAttributes(const char* /*attr*/, PyObject* /*obj*/)
{
    return 0;
}
