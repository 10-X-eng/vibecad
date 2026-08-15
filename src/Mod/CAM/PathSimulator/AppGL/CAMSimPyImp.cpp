// SPDX-License-Identifier: LGPL-2.1-or-later

/**************************************************************************
 *   Copyright (c) 2017 Shai Seger <shaise at gmail>                       *
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


#include <limits>

#include <Base/Interpreter.h>
#include <Base/PlacementPy.h>
#include <Base/PyWrapParseTupleAndKeywords.h>

#include <Gui/Document.h>
#include <Mod/Mesh/App/MeshPy.h>
#include <Mod/CAM/App/CommandPy.h>
#include <Mod/Part/App/TopoShapePy.h>

#include "DocumentPy.h"
// inclusion of the generated files (generated out of CAMSimPy.xml)
#include "CAMSimPy.h"
#include "CAMSimPy.cpp"


namespace CAMSimulator
{

// returns a string which represents the object e.g. when printed in python
std::string CAMSimPy::representation() const
{
    return std::string("<CAMSim object>");
}

PyObject* CAMSimPy::PyMake(struct _typeobject*, PyObject*, PyObject*)  // Python wrapper
{
    // create a new instance of CAMSimPy and the Twin object
    return new CAMSimPy(new CAMSim);
}

// constructor method
int CAMSimPy::PyInit(PyObject* /*args*/, PyObject* /*kwd*/)
{
    return 0;
}


PyObject* CAMSimPy::ResetSimulation(PyObject* args)
{
    PyObject* pObjDoc;
    if (!PyArg_ParseTuple(args, "O!", &(Gui::DocumentPy::Type), &pObjDoc)) {
        return nullptr;
    }
    CAMSim* sim = getCAMSimPtr();
    Gui::Document* doc = static_cast<Gui::DocumentPy*>(pObjDoc)->getDocumentPtr();
    sim->resetSimulation(doc);
    Py_IncRef(Py_None);
    return Py_None;
}

PyObject* CAMSimPy::BeginSimulation(PyObject* args, PyObject* kwds)
{
    static const std::array<const char*, 3> kwlist {"stock", "resolution", nullptr};
    PyObject* pObjStock;
    float resolution;
    if (!Base::Wrapped_ParseTupleAndKeywords(
            args,
            kwds,
            "O!f",
            kwlist,
            &(Part::TopoShapePy::Type),
            &pObjStock,
            &resolution
        )) {
        return nullptr;
    }
    CAMSim* sim = getCAMSimPtr();
    const Part::TopoShape& stock = *static_cast<Part::TopoShapePy*>(pObjStock)->getTopoShapePtr();
    sim->BeginSimulation(stock, resolution);
    Py_IncRef(Py_None);
    return Py_None;
}

PyObject* CAMSimPy::PrepareShapeMesh(PyObject* args, PyObject* kwds)
{
    static const std::array<const char*, 3> kwlist {"shape", "resolution", nullptr};
    PyObject* shapeObject;
    float resolution;
    if (!Base::Wrapped_ParseTupleAndKeywords(
            args,
            kwds,
            "O!f",
            kwlist,
            &(Part::TopoShapePy::Type),
            &shapeObject,
            &resolution
        )) {
        return nullptr;
    }

    const Part::TopoShape& shape
        = *static_cast<Part::TopoShapePy*>(shapeObject)->getTopoShapePtr();
    std::string preparedMesh;
    {
        Base::PyGILStateRelease release;
        preparedMesh = getCAMSimPtr()->PrepareShapeMesh(shape, resolution);
    }
    if (preparedMesh.size() > static_cast<std::size_t>(std::numeric_limits<Py_ssize_t>::max())) {
        PyErr_SetString(PyExc_OverflowError, "The prepared CAM simulator mesh is too large");
        return nullptr;
    }
    return PyBytes_FromStringAndSize(
        preparedMesh.data(),
        static_cast<Py_ssize_t>(preparedMesh.size())
    );
}

PyObject* CAMSimPy::BeginPreparedSimulation(PyObject* args, PyObject* kwds)
{
    static const std::array<const char*, 4>
        kwlist {"stock", "prepared_mesh", "quality", nullptr};
    PyObject* stockObject;
    PyObject* preparedMeshObject;
    float quality;
    if (!Base::Wrapped_ParseTupleAndKeywords(
            args,
            kwds,
            "O!O!f",
            kwlist,
            &(Part::TopoShapePy::Type),
            &stockObject,
            &PyBytes_Type,
            &preparedMeshObject,
            &quality
        )) {
        return nullptr;
    }

    char* preparedMeshData = nullptr;
    Py_ssize_t preparedMeshSize = 0;
    if (PyBytes_AsStringAndSize(
            preparedMeshObject,
            &preparedMeshData,
            &preparedMeshSize
        ) != 0) {
        return nullptr;
    }
    const Part::TopoShape& stock
        = *static_cast<Part::TopoShapePy*>(stockObject)->getTopoShapePtr();
    getCAMSimPtr()->BeginPreparedSimulation(
        stock,
        std::string_view(preparedMeshData, static_cast<std::size_t>(preparedMeshSize)),
        quality
    );
    Py_RETURN_NONE;
}

PyObject* CAMSimPy::AddTool(PyObject* args, PyObject* kwds)
{
    static const std::array<const char*, 5>
        kwlist {"shape", "toolnumber", "diameter", "resolution", nullptr};
    PyObject* pObjToolShape;
    int toolNumber;
    float resolution;
    float diameter;
    if (!Base::Wrapped_ParseTupleAndKeywords(
            args,
            kwds,
            "Oiff",
            kwlist,
            &pObjToolShape,
            &toolNumber,
            &diameter,
            &resolution
        )) {
        return nullptr;
    }
    // The tool shape is defined by a list of 2d points that represents the tool revolving profile
    Py_ssize_t num_floats = PyList_Size(pObjToolShape);
    std::vector<float> toolProfile;
    for (Py_ssize_t i = 0; i < num_floats; ++i) {
        PyObject* item = PyList_GetItem(pObjToolShape, i);
        toolProfile.push_back(static_cast<float>(PyFloat_AsDouble(item)));
    }

    CAMSim* sim = getCAMSimPtr();
    sim->addTool(toolProfile, toolNumber, diameter, resolution);

    Py_INCREF(Py_None);
    return Py_None;
}

PyObject* CAMSimPy::SetBaseShape(PyObject* args, PyObject* kwds)
{
    static const std::array<const char*, 3> kwlist {"shape", "resolution", nullptr};
    PyObject* pObjBaseShape;
    float resolution;
    if (!Base::Wrapped_ParseTupleAndKeywords(
            args,
            kwds,
            "O!f",
            kwlist,
            &(Part::TopoShapePy::Type),
            &pObjBaseShape,
            &resolution
        )) {
        return nullptr;
    }
    if (!PyArg_ParseTuple(args, "O!f", &(Part::TopoShapePy::Type), &pObjBaseShape, &resolution)) {
        return nullptr;
    }
    CAMSim* sim = getCAMSimPtr();
    const Part::TopoShape& baseShape
        = static_cast<Part::TopoShapePy*>(pObjBaseShape)->getTopoShapePtr()->getShape();
    sim->SetBaseShape(baseShape, resolution);

    Py_IncRef(Py_None);
    return Py_None;
}

PyObject* CAMSimPy::SetPreparedBaseShape(PyObject* args, PyObject* kwds)
{
    static const std::array<const char*, 3> kwlist {"shape", "prepared_mesh", nullptr};
    PyObject* shapeObject;
    PyObject* preparedMeshObject;
    if (!Base::Wrapped_ParseTupleAndKeywords(
            args,
            kwds,
            "O!O!",
            kwlist,
            &(Part::TopoShapePy::Type),
            &shapeObject,
            &PyBytes_Type,
            &preparedMeshObject
        )) {
        return nullptr;
    }

    char* preparedMeshData = nullptr;
    Py_ssize_t preparedMeshSize = 0;
    if (PyBytes_AsStringAndSize(
            preparedMeshObject,
            &preparedMeshData,
            &preparedMeshSize
        ) != 0) {
        return nullptr;
    }
    const Part::TopoShape& shape
        = *static_cast<Part::TopoShapePy*>(shapeObject)->getTopoShapePtr();
    getCAMSimPtr()->SetPreparedBaseShape(
        shape,
        std::string_view(preparedMeshData, static_cast<std::size_t>(preparedMeshSize))
    );
    Py_RETURN_NONE;
}

PyObject* CAMSimPy::AddCommand(PyObject* args)
{
    PyObject* pObjCmd;
    if (!PyArg_ParseTuple(args, "O!", &(Path::CommandPy::Type), &pObjCmd)) {
        return nullptr;
    }
    CAMSim* sim = getCAMSimPtr();
    Path::Command* cmd = static_cast<Path::CommandPy*>(pObjCmd)->getCommandPtr();
    sim->AddCommand(cmd);

    Py_INCREF(Py_None);
    return Py_None;
}

PyObject* CAMSimPy::AddGCode(PyObject* args)
{
    PyObject* commandObject;
    if (!PyArg_ParseTuple(args, "U", &commandObject)) {
        return nullptr;
    }
    Py_ssize_t commandSize = 0;
    const char* commandData = PyUnicode_AsUTF8AndSize(commandObject, &commandSize);
    if (!commandData) {
        return nullptr;
    }
    const std::string_view command(commandData, static_cast<std::size_t>(commandSize));
    if (command.find('\0') != std::string_view::npos) {
        PyErr_SetString(PyExc_ValueError, "CAM simulator G-code may not contain null bytes");
        return nullptr;
    }
    getCAMSimPtr()->AddGCode(command);
    Py_RETURN_NONE;
}

PyObject* CAMSimPy::getCustomAttributes(const char* /*attr*/) const
{
    return nullptr;
}

int CAMSimPy::setCustomAttributes(const char* /*attr*/, PyObject* /*obj*/)
{
    return 0;
}

}  // namespace CAMSimulator
