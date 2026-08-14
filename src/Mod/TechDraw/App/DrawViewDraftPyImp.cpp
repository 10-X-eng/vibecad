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

#include "DrawViewDraft.h"

#include <Mod/TechDraw/App/DrawViewSymbolPy.h>
#include <Mod/TechDraw/App/DrawViewDraftPy.h>
#include <Mod/TechDraw/App/DrawViewDraftPy.cpp>


using namespace TechDraw;


std::string DrawViewDraftPy::representation() const
{
    return std::string("<DrawViewDraft object>");
}

PyObject* DrawViewDraftPy::getPrecomputedDraft(PyObject* args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }
    const auto snapshot = getDrawViewDraftPtr()->getPrecomputedDraft();
    Py::Dict result;
    result.setItem("symbol", Py::String(snapshot.symbol));
    result.setItem("source_state_sha256", Py::String(snapshot.sourceState));
    return Py::new_reference_to(result);
}

PyObject* DrawViewDraftPy::setPrecomputedDraft(PyObject* args)
{
    PyObject* snapshot = nullptr;
    if (!PyArg_ParseTuple(args, "O!", &PyDict_Type, &snapshot)) {
        return nullptr;
    }
    if (PyDict_Size(snapshot) != 2) {
        throw Py::ValueError(
            "Draft snapshot must contain exactly symbol and source_state_sha256.");
    }
    PyObject* symbolObject = PyDict_GetItemString(snapshot, "symbol");
    PyObject* sourceStateObject = PyDict_GetItemString(snapshot, "source_state_sha256");
    if (!symbolObject || !sourceStateObject) {
        throw Py::ValueError("Draft snapshot is missing a required field.");
    }
    if (!PyUnicode_Check(symbolObject) || !PyUnicode_Check(sourceStateObject)) {
        throw Py::TypeError("Draft snapshot fields must be strings.");
    }
    Py::String symbol(symbolObject);
    Py::String sourceState(sourceStateObject);
    getDrawViewDraftPtr()->setPrecomputedDraft(
        symbol.as_std_string(), sourceState.as_std_string());
    Py_Return;
}

PyObject* DrawViewDraftPy::getCustomAttributes(const char* /*attr*/) const
{
    return nullptr;
}

int DrawViewDraftPy::setCustomAttributes(const char* /*attr*/, PyObject* /*obj*/)
{
    return 0;
}
