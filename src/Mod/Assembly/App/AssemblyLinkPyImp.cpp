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


// inclusion of the generated files (generated out of AssemblyLink.xml)
#include "AssemblyLinkPy.h"
#include "AssemblyLinkPy.cpp"

#include <App/DocumentObjectPy.h>

using namespace Assembly;

// returns a string which represents the object e.g. when printed in python
std::string AssemblyLinkPy::representation() const
{
    return {"<Assembly link>"};
}

PyObject* AssemblyLinkPy::getCustomAttributes(const char* /*attr*/) const
{
    return nullptr;
}

int AssemblyLinkPy::setCustomAttributes(const char* /*attr*/, PyObject* /*obj*/)
{
    return 0;
}

Py::List AssemblyLinkPy::getJoints() const
{
    Py::List ret;
    std::vector<App::DocumentObject*> list = getAssemblyLinkPtr()->getJoints();

    for (auto It : list) {
        ret.append(Py::Object(It->getPyObject(), true));
    }

    return ret;
}

PyObject* AssemblyLinkPy::synchronizeContents(PyObject* args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }

    getAssemblyLinkPtr()->updateContents();
    Py_Return;
}

PyObject* AssemblyLinkPy::synchronizeContentsWithResourceMap(PyObject* args)
{
    PyObject* pyOldResources = nullptr;
    if (!PyArg_ParseTuple(args, "O", &pyOldResources)) {
        return nullptr;
    }
    if (!PySequence_Check(pyOldResources)) {
        PyErr_SetString(
            PyExc_TypeError,
            "Expected one sequence of AssemblyLink resource objects"
        );
        return nullptr;
    }

    PY_TRY
    {
        Py::Sequence sequence(pyOldResources);
        std::vector<App::DocumentObject*> oldResources;
        oldResources.reserve(sequence.size());
        for (Py_ssize_t index = 0; index < sequence.size(); ++index) {
            const auto item = sequence[index];
            if (!PyObject_TypeCheck(
                    item.ptr(),
                    &App::DocumentObjectPy::Type
                )) {
                throw Py::TypeError(
                    "Every AssemblyLink resource must be a document object"
                );
            }
            oldResources.push_back(
                static_cast<App::DocumentObjectPy*>(item.ptr())
                    ->getDocumentObjectPtr()
            );
        }

        const auto synchronization =
            getAssemblyLinkPtr()->synchronizeContentsWithResourceMap(
                oldResources
            );

        Py::List finalResources;
        for (auto* resource : synchronization.orderedFinalResources) {
            finalResources.append(
                Py::Object(resource->getPyObject(), true)
            );
        }

        Py::List oldToFinal;
        for (std::size_t index = 0;
             index
             < synchronization.orderedOldResourceIdentities.size();
             ++index) {
            const auto& identity =
                synchronization.orderedOldResourceIdentities[index];
            auto* finalResource =
                synchronization.oldToFinalResources[index];
            Py::Tuple mapping(3);
            mapping.setItem(0, Py::Long(identity.objectId));
            mapping.setItem(1, Py::String(identity.objectName));
            mapping.setItem(
                2,
                finalResource
                    ? Py::Object(finalResource->getPyObject(), true)
                    : Py::None()
            );
            oldToFinal.append(mapping);
        }

        Py::List retired;
        for (const auto& identity :
             synchronization.retiredResourceIdentities) {
            Py::Tuple item(2);
            item.setItem(0, Py::Long(identity.objectId));
            item.setItem(1, Py::String(identity.objectName));
            retired.append(item);
        }

        Py::Dict result;
        result["final_resources"] = finalResources;
        result["old_to_final"] = oldToFinal;
        result["retired"] = retired;
        return Py::new_reference_to(result);
    }
    PY_CATCH;
}
