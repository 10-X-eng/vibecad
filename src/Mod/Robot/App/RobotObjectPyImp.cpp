// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2010 Jürgen Riegel <juergen.riegel@web.de>              *
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

#include <cmath>
#include <memory>
#include <sstream>

#include <Base/Exception.h>

// clang-format off
// inclusion of the generated files (generated out of RobotObjectPy.xml)
#include "RobotObjectPy.h"
#include "RobotObjectPy.cpp"
// clang-format on

#include "Robot6AxisPy.h"


using namespace Robot;

// returns a string which represents the object e.g. when printed in python
std::string RobotObjectPy::representation() const
{
    return {"<RobotObject object>"};
}


PyObject* RobotObjectPy::getRobot(PyObject* /*args*/)
{
    return new Robot6AxisPy(new Robot6Axis(getRobotObjectPtr()->getRobot()));
}

PyObject* RobotObjectPy::setKinematic(PyObject* args, PyObject* kwd)
{
    PyObject* axesObject = nullptr;
    static char axesKeyword[] = "axes";
    static char* keywords[] = {axesKeyword, nullptr};
    if (!PyArg_ParseTupleAndKeywords(
            args,
            kwd,
            "O:setKinematic",
            keywords,
            &axesObject
        )) {
        return nullptr;
    }

    std::unique_ptr<PyObject, decltype(&Py_DecRef)> axes(
        PySequence_Fast(axesObject, "axes must be a sequence of six rows"),
        &Py_DecRef
    );
    if (!axes) {
        return nullptr;
    }
    if (PySequence_Fast_GET_SIZE(axes.get()) != 6) {
        PyErr_SetString(PyExc_ValueError, "axes must contain exactly six rows");
        return nullptr;
    }

    AxisDefinition definitions[6];
    for (Py_ssize_t axis = 0; axis < 6; ++axis) {
        PyObject* rowObject = PySequence_Fast_GET_ITEM(axes.get(), axis);
        std::unique_ptr<PyObject, decltype(&Py_DecRef)> row(
            PySequence_Fast(rowObject, "each axis row must be a sequence"),
            &Py_DecRef
        );
        if (!row) {
            return nullptr;
        }
        if (PySequence_Fast_GET_SIZE(row.get()) != 8) {
            PyErr_Format(
                PyExc_ValueError,
                "axis row %zd must contain exactly eight numbers",
                axis
            );
            return nullptr;
        }
        double values[8];
        for (Py_ssize_t column = 0; column < 8; ++column) {
            values[column] = PyFloat_AsDouble(PySequence_Fast_GET_ITEM(row.get(), column));
            if (PyErr_Occurred()) {
                return nullptr;
            }
            if (!std::isfinite(values[column])) {
                PyErr_Format(
                    PyExc_ValueError,
                    "axis row %zd column %zd must be finite",
                    axis,
                    column
                );
                return nullptr;
            }
        }
        if (values[4] != -1.0 && values[4] != 1.0) {
            PyErr_Format(
                PyExc_ValueError,
                "axis row %zd rotation direction must be -1 or 1",
                axis
            );
            return nullptr;
        }
        if (values[6] > values[5]) {
            PyErr_Format(
                PyExc_ValueError,
                "axis row %zd minimum angle must not exceed maximum angle",
                axis
            );
            return nullptr;
        }
        if (values[7] <= 0.0) {
            PyErr_Format(
                PyExc_ValueError,
                "axis row %zd maximum velocity must be greater than zero",
                axis
            );
            return nullptr;
        }
        definitions[axis] = {
            values[0],
            values[1],
            values[2],
            values[3],
            values[4],
            values[5],
            values[6],
            values[7],
        };
    }

    getRobotObjectPtr()->setKinematic(definitions);
    Py_RETURN_NONE;
}


PyObject* RobotObjectPy::getCustomAttributes(const char* /*attr*/) const
{
    return nullptr;
}

int RobotObjectPy::setCustomAttributes(const char* /*attr*/, PyObject* /*obj*/)
{
    return 0;
}
