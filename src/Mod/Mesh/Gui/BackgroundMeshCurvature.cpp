// SPDX-License-Identifier: LGPL-2.1-or-later

#include "BackgroundMeshCurvature.h"

#include <Base/Exception.h>
#include <Base/Interpreter.h>
#include <Mod/Mesh/App/MeshFeature.h>

void MeshGui::startBackgroundMeshCurvature(
    const std::vector<Mesh::Feature*>& sources
)
{
    if (sources.empty()) {
        throw Base::ValueError("Select at least one Mesh for curvature");
    }
    auto* document = sources.front() ? sources.front()->getDocument() : nullptr;
    if (!document) {
        throw Base::ValueError("The Mesh curvature document is unavailable");
    }
    try {
        Base::PyGILStateLocker lock;
        Py::List pythonSources;
        for (auto* source : sources) {
            if (!source || source->getDocument() != document) {
                throw Base::ValueError(
                    "Every Mesh curvature source must belong to one document"
                );
            }
            pythonSources.append(Py::asObject(source->getPyObject()));
        }
        PyObject* imported = PyImport_ImportModule("VibeCADMeshCurvatureGui");
        if (!imported) {
            throw Py::Exception();
        }
        Py::Module module(imported, true);
        module.callMemberFunction(
            "start_mesh_curvature",
            Py::TupleN(pythonSources)
        );
    }
    catch (const Py::Exception&) {
        Base::PyException error;
        throw Base::RuntimeError(error.what());
    }
}
