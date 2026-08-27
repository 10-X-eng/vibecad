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


#include <Base/Interpreter.h>
#include <Base/PlacementPy.h>
#include <Base/PyWrapParseTupleAndKeywords.h>

#include <Mod/Mesh/App/MeshPy.h>
#include <Mod/CAM/App/CommandPy.h>
#include <Mod/Part/App/TopoShapePy.h>

#include "PathSim.h"
// inclusion of the generated files (generated out of PathSimPy.xml)
#include "PathSimPy.h"
#include "PathSimPy.cpp"


using namespace PathSimulator;

// returns a string which represents the object e.g. when printed in python
std::string PathSimPy::representation() const
{
    return std::string("<PathSim object>");
}

PyObject* PathSimPy::PyMake(struct _typeobject*, PyObject*, PyObject*)  // Python wrapper
{
    // create a new instance of PathSimPy and the Twin object
    return new PathSimPy(new PathSim);
}

// constructor method
int PathSimPy::PyInit(PyObject* /*args*/, PyObject* /*kwd*/)
{
    return 0;
}


PyObject* PathSimPy::BeginSimulation(PyObject* args, PyObject* kwds)
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
    PathSim* sim = getPathSimPtr();
    Part::TopoShape stock
        = *static_cast<Part::TopoShapePy*>(pObjStock)->getTopoShapePtr();
    {
        Base::PyGILStateRelease release;
        sim->BeginSimulation(&stock, resolution);
    }
    Py_IncRef(Py_None);
    return Py_None;
}

PyObject* PathSimPy::SetToolShape(PyObject* args)
{
    PyObject* pObjToolShape;
    float resolution;
    if (!PyArg_ParseTuple(args, "O!f", &(Part::TopoShapePy::Type), &pObjToolShape, &resolution)) {
        return nullptr;
    }
    PathSim* sim = getPathSimPtr();
    const TopoDS_Shape toolShape
        = static_cast<Part::TopoShapePy*>(pObjToolShape)->getTopoShapePtr()->getShape();
    {
        Base::PyGILStateRelease release;
        sim->SetToolShape(toolShape, resolution);
    }
    Py_IncRef(Py_None);
    return Py_None;
}

PyObject* PathSimPy::GetResultMesh(PyObject* args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }
    cStock* stock = getPathSimPtr()->m_stock.get();
    if (!stock) {
        PyErr_SetString(PyExc_RuntimeError, "Simulation has no stock object");
        return nullptr;
    }

    auto meshOuter = std::make_unique<Mesh::MeshObject>();
    auto meshInner = std::make_unique<Mesh::MeshObject>();
    {
        Base::PyGILStateRelease release;
        stock->Tessellate(*meshOuter, *meshInner);
    }
    Mesh::MeshPy* meshOuterpy = new Mesh::MeshPy(meshOuter.release());
    Mesh::MeshPy* meshInnerpy = new Mesh::MeshPy(meshInner.release());
    PyObject* tuple = PyTuple_New(2);
    PyTuple_SetItem(tuple, 0, meshOuterpy);
    PyTuple_SetItem(tuple, 1, meshInnerpy);
    return tuple;
}

PyObject* PathSimPy::GetCombinedResultMesh(PyObject* args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }
    cStock* stock = getPathSimPtr()->m_stock.get();
    if (!stock) {
        PyErr_SetString(PyExc_RuntimeError, "Simulation has no stock object");
        return nullptr;
    }

    auto meshOuter = std::make_unique<Mesh::MeshObject>();
    auto meshInner = std::make_unique<Mesh::MeshObject>();
    {
        Base::PyGILStateRelease release;
        stock->Tessellate(*meshOuter, *meshInner);
        meshOuter->addMesh(*meshInner);
    }
    return new Mesh::MeshPy(meshOuter.release());
}

PyObject* PathSimPy::GetRemainingStockShape(PyObject* args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }
    cStock* stock = getPathSimPtr()->m_stock.get();
    if (!stock) {
        PyErr_SetString(PyExc_RuntimeError, "Simulation has no stock object");
        return nullptr;
    }

    TopoDS_Shape shape;
    {
        Base::PyGILStateRelease release;
        shape = stock->RemainingStockShape();
    }
    return new Part::TopoShapePy(new Part::TopoShape(shape));
}

PyObject* PathSimPy::GetSimulationStats(PyObject* args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }
    PathSim* sim = getPathSimPtr();
    try {
        cStock::Statistics stats;
        {
            Base::PyGILStateRelease release;
            stats = sim->GetSimulationStats();
        }
        PyObject* result = PyDict_New();
        const auto setOwned = [result](const char* name, PyObject* value) {
            PyDict_SetItemString(result, name, value);
            Py_DECREF(value);
        };
        setOwned("resolution_mm", PyFloat_FromDouble(stats.resolution));
        PyObject* grid = Py_BuildValue("[ii]", stats.gridX, stats.gridY);
        PyDict_SetItemString(result, "grid", grid);
        Py_DECREF(grid);
        setOwned("initial_volume_mm3", PyFloat_FromDouble(stats.initialVolume));
        setOwned("removed_volume_mm3", PyFloat_FromDouble(stats.removedVolume));
        setOwned("remaining_volume_mm3", PyFloat_FromDouble(stats.remainingVolume));
        setOwned("modified_cells", PyLong_FromLong(stats.modifiedCells));
        setOwned("cut_commands", PyLong_FromLong(sim->m_cutCommands));
        setOwned("rapid_commands", PyLong_FromLong(sim->m_rapidCommands));
        setOwned("unsupported_commands", PyLong_FromLong(sim->m_unsupportedCommands));
        if (stats.hasRemovedBounds) {
            PyObject* minimum
                = Py_BuildValue("[ddd]", stats.removedMin.x, stats.removedMin.y, stats.removedMin.z);
            PyObject* maximum
                = Py_BuildValue("[ddd]", stats.removedMax.x, stats.removedMax.y, stats.removedMax.z);
            PyObject* bounds = PyDict_New();
            PyDict_SetItemString(bounds, "min", minimum);
            PyDict_SetItemString(bounds, "max", maximum);
            Py_DECREF(minimum);
            Py_DECREF(maximum);
            PyDict_SetItemString(result, "removed_bounds", bounds);
            Py_DECREF(bounds);
        }
        else {
            PyDict_SetItemString(result, "removed_bounds", Py_None);
        }
        return result;
    }
    catch (const Base::Exception& exc) {
        PyErr_SetString(PyExc_RuntimeError, exc.what());
        return nullptr;
    }
}


PyObject* PathSimPy::ApplyCommand(PyObject* args, PyObject* kwds)
{
    static const std::array<const char*, 3> kwlist {"position", "command", nullptr};
    PyObject* pObjPlace;
    PyObject* pObjCmd;
    if (!Base::Wrapped_ParseTupleAndKeywords(
            args,
            kwds,
            "O!O!",
            kwlist,
            &(Base::PlacementPy::Type),
            &pObjPlace,
            &(Path::CommandPy::Type),
            &pObjCmd
        )) {
        return nullptr;
    }
    PathSim* sim = getPathSimPtr();
    Base::Placement pos
        = *static_cast<Base::PlacementPy*>(pObjPlace)->getPlacementPtr();
    Path::Command cmd
        = *static_cast<Path::CommandPy*>(pObjCmd)->getCommandPtr();
    std::unique_ptr<Base::Placement> newpos;
    {
        Base::PyGILStateRelease release;
        newpos.reset(sim->ApplyCommand(&pos, &cmd));
    }
    // Base::Console().log("Done...\n");
    // Base::Console().Refresh();
    Base::PlacementPy* newposPy = new Base::PlacementPy(newpos.release());
    return newposPy;
}

Py::Object PathSimPy::getTool() const
{
    // return Py::Object();
    throw Py::AttributeError("Not yet implemented");
}

PyObject* PathSimPy::getCustomAttributes(const char* /*attr*/) const
{
    return nullptr;
}

int PathSimPy::setCustomAttributes(const char* /*attr*/, PyObject* /*obj*/)
{
    return 0;
}
