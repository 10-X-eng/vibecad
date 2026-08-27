// SPDX-License-Identifier: LGPL-2.1-or-later

#include "BackgroundMeshModification.h"

#include <App/Document.h>
#include <Base/Exception.h>
#include <Base/Interpreter.h>
#include <Mod/Mesh/App/MeshFeature.h>

void MeshGui::startBackgroundMeshModification(
    const std::vector<BackgroundMeshModificationTarget>& targets,
    const char* operation,
    const std::string& argumentsJson
)
{
    if (targets.empty() || !operation || operation[0] == '\0') {
        throw Base::ValueError("The background Mesh modification request is incomplete");
    }
    App::Document* document = targets.front().source ? targets.front().source->getDocument() : nullptr;
    if (!document) {
        throw Base::ValueError("The background Mesh modification document is unavailable");
    }

    try {
        Base::PyGILStateLocker lock;
        Py::List entries;
        for (const auto& target : targets) {
            if (!target.source || target.source->getDocument() != document) {
                throw Base::ValueError("Every background Mesh target must belong to one document");
            }
            Py::List points;
            for (long index : target.pointIndices) {
                points.append(Py::Long(index));
            }
            Py::List facets;
            for (long index : target.facetIndices) {
                facets.append(Py::Long(index));
            }
            entries.append(Py::TupleN(
                Py::asObject(target.source->getPyObject()),
                Py::String(target.label),
                points,
                facets
            ));
        }
        PyObject* imported = PyImport_ImportModule("VibeCADMeshModificationGui");
        if (!imported) {
            throw Py::Exception();
        }
        Py::Module module(imported, true);
        module.callMemberFunction("start_mesh_modifications", Py::TupleN(
            entries,
            Py::String(operation),
            Py::String(argumentsJson)
        ));
    }
    catch (const Py::Exception&) {
        Base::PyException error;
        throw Base::RuntimeError(error.what());
    }
}
