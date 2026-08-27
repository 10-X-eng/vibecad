// SPDX-License-Identifier: LGPL-2.1-or-later

#include "BackgroundMeshCut.h"

#include <Base/Exception.h>
#include <Base/Interpreter.h>

void MeshGui::startBackgroundMeshCut(
    const char* operation,
    const std::string& argumentsJson
)
{
    if (!operation || operation[0] == '\0') {
        throw Base::ValueError("The background Mesh-cut request is incomplete");
    }
    try {
        Base::PyGILStateLocker lock;
        PyObject* imported = PyImport_ImportModule("VibeCADMeshCutGui");
        if (!imported) {
            throw Py::Exception();
        }
        Py::Module module(imported, true);
        module.callMemberFunction(
            "start_mesh_cut",
            Py::TupleN(Py::String(operation), Py::String(argumentsJson))
        );
    }
    catch (const Py::Exception&) {
        Base::PyException error;
        throw Base::RuntimeError(error.what());
    }
}
