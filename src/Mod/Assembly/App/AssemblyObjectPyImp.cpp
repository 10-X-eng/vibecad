// SPDX-License-Identifier: LGPL-2.1-or-later
/****************************************************************************
 *                                                                          *
 *   Copyright (c) 2024 Ondsel <development@ondsel.com>                     *
 *                                                                          *
 *   This file is part of FreeCAD.                                          *
 *                                                                          *
 *   FreeCAD is free software: you can redistribute it and/or modify it     *
 *   under the terms of the GNU Lesser General Public License as            *
 *   published by the Free Software Foundation, either version 2.1 of the   *
 *   License, or (at your option) any later version.                        *
 *                                                                          *
 *   FreeCAD is distributed in the hope that it will be useful, but         *
 *   WITHOUT ANY WARRANTY; without even the implied warranty of             *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU       *
 *   Lesser General Public License for more details.                        *
 *                                                                          *
 *   You should have received a copy of the GNU Lesser General Public       *
 *   License along with FreeCAD. If not, see                                *
 *   <https://www.gnu.org/licenses/>.                                       *
 *                                                                          *
 ***************************************************************************/


// inclusion of the generated files (generated out of AssemblyObject.xml)
#include "AssemblyObjectPy.h"
#include "AssemblyObjectPy.cpp"

#include <algorithm>

#include <App/PropertyLinks.h>
#include <Base/PlacementPy.h>

using namespace Assembly;

// returns a string which represents the object e.g. when printed in python
std::string AssemblyObjectPy::representation() const
{
    return {"<Assembly object>"};
}

PyObject* AssemblyObjectPy::getCustomAttributes(const char* /*attr*/) const
{
    return nullptr;
}

int AssemblyObjectPy::setCustomAttributes(const char* /*attr*/, PyObject* /*obj*/)
{
    return 0;
}

PyObject* AssemblyObjectPy::solve(PyObject* args) const
{
    PyObject* enableUndoPy;
    bool enableUndo;

    if (!PyArg_ParseTuple(args, "O!", &PyBool_Type, &enableUndoPy)) {
        PyErr_Clear();
        if (!PyArg_ParseTuple(args, "")) {
            return nullptr;
        }
        else {
            enableUndo = false;
        }
    }
    else {
        enableUndo = Base::asBoolean(enableUndoPy);
    }

    int ret = this->getAssemblyObjectPtr()->solve(enableUndo);
    return Py_BuildValue("i", ret);
}

PyObject* AssemblyObjectPy::generateSimulation(PyObject* args) const
{
    PyObject* pyobj;

    if (!PyArg_ParseTuple(args, "O", &pyobj)) {
        return nullptr;
    }
    auto* obj = static_cast<App::DocumentObjectPy*>(pyobj)->getDocumentObjectPtr();
    int ret = this->getAssemblyObjectPtr()->generateSimulation(obj);
    return Py_BuildValue("i", ret);
}

PyObject* AssemblyObjectPy::ensureIdentityPlacements(PyObject* args) const
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }
    this->getAssemblyObjectPtr()->ensureIdentityPlacements();
    Py_Return;
}

PyObject* AssemblyObjectPy::startSimulation(PyObject* args) const
{
    PyObject* object;
    if (!PyArg_ParseTuple(args, "O!", &App::DocumentObjectPy::Type, &object)) {
        return nullptr;
    }
    PY_TRY {
        return PyLong_FromUnsignedLongLong(getAssemblyObjectPtr()->startSimulation(
            static_cast<App::DocumentObjectPy*>(object)->getDocumentObjectPtr()));
    } PY_CATCH;
    Py_Return;
}

PyObject* AssemblyObjectPy::startSimulationPlayback(PyObject* args) const
{
    PyObject* object;
    if (!PyArg_ParseTuple(args, "O!", &App::DocumentObjectPy::Type, &object)) { return nullptr; }
    PY_TRY {
        return PyLong_FromUnsignedLongLong(getAssemblyObjectPtr()->startSimulationPlayback(
            static_cast<App::DocumentObjectPy*>(object)->getDocumentObjectPtr()));
    } PY_CATCH;
    return nullptr;
}

PyObject* AssemblyObjectPy::finishSimulation(PyObject* args) const
{
    unsigned long long request = 0;
    if (!PyArg_ParseTuple(args, "|K", &request)) { return nullptr; }
    PY_TRY {
        return PyBool_FromLong(getAssemblyObjectPtr()->finishSimulation(request));
    } PY_CATCH;
    return nullptr;
}

PyObject* AssemblyObjectPy::cancelSimulation(PyObject* args) const
{
    unsigned long long request = 0;
    if (!PyArg_ParseTuple(args, "|K", &request)) { return nullptr; }
    PY_TRY { getAssemblyObjectPtr()->cancelSimulation(request); } PY_CATCH;
    Py_Return;
}

PyObject* AssemblyObjectPy::requestSimulationFrame(PyObject* args) const
{
    unsigned long index = 0;
    if (!PyArg_ParseTuple(args, "k", &index)) { return nullptr; }
    PY_TRY {
        return PyLong_FromUnsignedLongLong(getAssemblyObjectPtr()->requestSimulationFrame(index));
    } PY_CATCH;
    return nullptr;
}

PyObject* AssemblyObjectPy::getSimulationFrame(PyObject* args) const
{
    unsigned long index = 0;
    if (!PyArg_ParseTuple(args, "k", &index)) { return nullptr; }
    PY_TRY {
        Py::List result;
        for (const auto& [name, placement] :
             getAssemblyObjectPtr()->getSimulationFrame(index)) {
            Py::Tuple item(2);
            item.setItem(0, Py::String(name));
            item.setItem(
                1,
                Py::asObject(new Base::PlacementPy(new Base::Placement(placement)))
            );
            result.append(item);
        }
        return Py::new_reference_to(result);
    } PY_CATCH;
    return nullptr;
}

PyObject* AssemblyObjectPy::setSimulationPresentation(PyObject* args) const
{
    int active = 0;
    if (!PyArg_ParseTuple(args, "p", &active)) { return nullptr; }
    PY_TRY {
        return PyBool_FromLong(getAssemblyObjectPtr()->setSimulationPresentation(active != 0));
    } PY_CATCH;
    return nullptr;
}

PyObject* AssemblyObjectPy::finishSimulationFrame(PyObject* args) const
{
    unsigned long long request = 0;
    if (!PyArg_ParseTuple(args, "K", &request)) { return nullptr; }
    PY_TRY {
        return PyBool_FromLong(getAssemblyObjectPtr()->finishSimulationFrame(request));
    } PY_CATCH;
    return nullptr;
}

PyObject* AssemblyObjectPy::takeSimulationFrame(PyObject* args) const
{
    unsigned long long request = 0;
    if (!PyArg_ParseTuple(args, "K", &request)) { return nullptr; }
    PY_TRY {
        auto frame = getAssemblyObjectPtr()->takeSimulationFrame(request);
        if (!frame) { Py_Return; }
        Py::List result;
        for (const auto& [name, placement] : *frame) {
            Py::Tuple item(2);
            item.setItem(0, Py::String(name));
            item.setItem(
                1,
                Py::asObject(new Base::PlacementPy(new Base::Placement(placement)))
            );
            result.append(item);
        }
        return Py::new_reference_to(result);
    } PY_CATCH;
    return nullptr;
}

PyObject* AssemblyObjectPy::cancelSimulationFrame(PyObject* args) const
{
    unsigned long long request = 0;
    if (!PyArg_ParseTuple(args, "|K", &request)) { return nullptr; }
    PY_TRY { getAssemblyObjectPtr()->cancelSimulationFrame(request); } PY_CATCH;
    Py_Return;
}

PyObject* AssemblyObjectPy::updateForFrame(PyObject* args) const
{
    unsigned long index {};

    if (!PyArg_ParseTuple(args, "k", &index)) {
        throw Py::RuntimeError("updateForFrame requires an integer index");
    }
    PY_TRY
    {
        this->getAssemblyObjectPtr()->updateForFrame(index);
    }
    PY_CATCH;

    Py_Return;
}

PyObject* AssemblyObjectPy::numberOfFrames(PyObject* args) const
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }
    size_t ret = this->getAssemblyObjectPtr()->numberOfFrames();
    return Py_BuildValue("k", ret);
}

PyObject* AssemblyObjectPy::updateSolveStatus(PyObject* args) const
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }

    this->getAssemblyObjectPtr()->updateSolveStatus();
    Py_Return;
}

PyObject* AssemblyObjectPy::getSolverDiagnostics(PyObject* args) const
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }

    auto* assembly = this->getAssemblyObjectPtr();
    auto stringList = [](const std::vector<std::string>& values) {
        Py::List result;
        for (const auto& value : values) {
            result.append(Py::String(value));
        }
        return result;
    };

    const auto& conflicting = assembly->getLastConflicting();
    const auto& redundant = assembly->getLastRedundant();
    const auto& partial = assembly->getLastPartiallyRedundant();
    const auto& malformed = assembly->getLastMalformed();

    Py::List jointDiagnostics;
    for (const auto& native : assembly->getLastJointDiagnostics()) {
        Py::Dict joint;
        joint["joint"] = Py::String(native.jointName);
        joint["constraint_count"] = Py::Long(native.constraintCount);
        joint["removed_degrees_of_freedom"] = Py::Long(native.removedDegreesOfFreedom);
        joint["redundant_constraint_count"] = Py::Long(native.redundantConstraintCount);
        joint["maximum_absolute_residual"] = Py::Float(native.maximumAbsoluteResidual);
        std::string status = "satisfied";
        if (std::find(malformed.begin(), malformed.end(), native.jointName) != malformed.end()) {
            status = "malformed";
        }
        else if (std::find(conflicting.begin(), conflicting.end(), native.jointName)
                 != conflicting.end()) {
            status = "conflicting";
        }
        else if (std::find(redundant.begin(), redundant.end(), native.jointName)
                 != redundant.end()) {
            status = "redundant";
        }
        else if (std::find(partial.begin(), partial.end(), native.jointName) != partial.end()) {
            status = "partially_redundant";
        }
        joint["status"] = Py::String(status);
        Py::List constraints;
        for (const auto& nativeConstraint : native.constraints) {
            Py::Dict constraint;
            constraint["specification"] = Py::String(nativeConstraint.specification);
            constraint["residual"] = Py::Float(nativeConstraint.residual);
            constraint["absolute_residual"] = Py::Float(std::abs(nativeConstraint.residual));
            constraint["redundant"] = Py::Boolean(nativeConstraint.redundant);
            constraints.append(constraint);
        }
        joint["constraints"] = constraints;
        jointDiagnostics.append(joint);
    }

    Py::List grounded;
    for (auto* groundedJoint : assembly->getGroundedJoints()) {
        if (!groundedJoint) {
            continue;
        }
        Py::Dict item;
        item["joint"] = Py::String(groundedJoint->getNameInDocument());
        auto* link = groundedJoint->getPropertyByName<App::PropertyLink>("ObjectToGround");
        auto* component = link ? link->getValue() : nullptr;
        item["component"] = component
            ? Py::String(component->getNameInDocument())
            : Py::String("");
        grounded.append(item);
    }

    Py::Dict result;
    result["solver_status"] = Py::Long(assembly->getLastSolverStatus());
    result["solver_message"] = Py::String(assembly->getLastSolverMessage());
    result["remaining_degrees_of_freedom"] = Py::Long(assembly->getLastDoF());
    result["has_conflicts"] = Py::Boolean(assembly->getLastHasConflicts());
    result["has_redundancies"] = Py::Boolean(assembly->getLastHasRedundancies());
    result["has_partial_redundancies"] = Py::Boolean(
        assembly->getLastHasPartialRedundancies()
    );
    result["has_malformed_constraints"] = Py::Boolean(
        assembly->getLastHasMalformedConstraints()
    );
    result["conflicting_joints"] = stringList(conflicting);
    result["redundant_joints"] = stringList(redundant);
    result["partially_redundant_joints"] = stringList(partial);
    result["malformed_joints"] = stringList(malformed);
    result["grounded_components"] = grounded;
    result["joints"] = jointDiagnostics;
    result["residual_tolerance"] = Py::Float(1.0e-6);
    return Py::new_reference_to(result);
}

PyObject* AssemblyObjectPy::undoSolve(PyObject* args) const
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }
    this->getAssemblyObjectPtr()->undoSolve();
    Py_Return;
}

PyObject* AssemblyObjectPy::clearUndo(PyObject* args) const
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }
    this->getAssemblyObjectPtr()->clearUndo();
    Py_Return;
}

PyObject* AssemblyObjectPy::isPartConnected(PyObject* args) const
{
    PyObject* pyobj;

    if (!PyArg_ParseTuple(args, "O!", &(App::DocumentObjectPy::Type), &pyobj)) {
        return nullptr;
    }
    auto* obj = static_cast<App::DocumentObjectPy*>(pyobj)->getDocumentObjectPtr();
    bool ok = this->getAssemblyObjectPtr()->isPartConnected(obj);
    return Py_BuildValue("O", (ok ? Py_True : Py_False));
}

PyObject* AssemblyObjectPy::isPartGrounded(PyObject* args) const
{
    PyObject* pyobj;

    if (!PyArg_ParseTuple(args, "O!", &(App::DocumentObjectPy::Type), &pyobj)) {
        return nullptr;
    }
    auto* obj = static_cast<App::DocumentObjectPy*>(pyobj)->getDocumentObjectPtr();
    bool ok = this->getAssemblyObjectPtr()->isPartGrounded(obj);
    return Py_BuildValue("O", (ok ? Py_True : Py_False));
}

PyObject* AssemblyObjectPy::isJointConnectingPartToGround(PyObject* args) const
{
    PyObject* pyobj;
    char* pname;

    if (!PyArg_ParseTuple(args, "O!s", &(App::DocumentObjectPy::Type), &pyobj, &pname)) {
        return nullptr;
    }
    auto* obj = static_cast<App::DocumentObjectPy*>(pyobj)->getDocumentObjectPtr();
    bool ok = this->getAssemblyObjectPtr()->isJointConnectingPartToGround(obj, pname);
    return Py_BuildValue("O", (ok ? Py_True : Py_False));
}

PyObject* AssemblyObjectPy::exportAsASMT(PyObject* args) const
{
    char* utf8Name;
    if (!PyArg_ParseTuple(args, "et", "utf-8", &utf8Name)) {
        return nullptr;
    }

    std::string fileName = utf8Name;
    PyMem_Free(utf8Name);

    if (fileName.empty()) {
        PyErr_SetString(PyExc_ValueError, "Passed string is empty");
        return nullptr;
    }

    this->getAssemblyObjectPtr()->exportAsASMT(fileName);

    Py_Return;
}

Py::List AssemblyObjectPy::getJoints() const
{
    Py::List ret;
    std::vector<App::DocumentObject*> list = getAssemblyObjectPtr()->getJoints();

    for (auto It : list) {
        ret.append(Py::Object(It->getPyObject(), true));
    }

    return ret;
}

PyObject* AssemblyObjectPy::getDownstreamParts(PyObject* args) const
{
    PyObject* pyPart;
    PyObject* pyJoint;

    // Parse the two arguments: a part object and a joint object
    if (!PyArg_ParseTuple(
            args,
            "O!O!",
            &(App::DocumentObjectPy::Type),
            &pyPart,
            &(App::DocumentObjectPy::Type),
            &pyJoint
        )) {
        return nullptr;
    }

    auto* part = static_cast<App::DocumentObjectPy*>(pyPart)->getDocumentObjectPtr();
    auto* joint = static_cast<App::DocumentObjectPy*>(pyJoint)->getDocumentObjectPtr();

    // Call the C++ method
    std::vector<Assembly::ObjRef> downstreamParts
        = this->getAssemblyObjectPtr()->getDownstreamParts(part, joint);

    // Convert the result into a Python list of DocumentObjects
    Py::List ret;
    for (const auto& objRef : downstreamParts) {
        if (objRef.obj) {
            ret.append(Py::Object(objRef.obj->getPyObject(), true));
        }
    }

    return Py::new_reference_to(ret);
}
