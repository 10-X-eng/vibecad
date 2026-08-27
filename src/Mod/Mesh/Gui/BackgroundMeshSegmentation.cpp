// SPDX-License-Identifier: LGPL-2.1-or-later

#include "BackgroundMeshSegmentation.h"

#include <App/Document.h>
#include <Base/Exception.h>
#include <Base/Interpreter.h>
#include <Mod/Mesh/App/MeshFeature.h>

void MeshGui::startBackgroundMeshSegmentation(
    const std::vector<Mesh::Feature*>& sources,
    const char* operation,
    const std::string& argumentsJson
)
{
    if (sources.empty() || !operation || operation[0] == '\0') {
        throw Base::ValueError("The background Mesh segmentation request is incomplete");
    }
    App::Document* document = sources.front() ? sources.front()->getDocument() : nullptr;
    if (!document) {
        throw Base::ValueError("The background Mesh segmentation document is unavailable");
    }
    try {
        Base::PyGILStateLocker lock;
        Py::List pythonSources;
        for (auto* source : sources) {
            if (!source || source->getDocument() != document) {
                throw Base::ValueError(
                    "Every background Mesh segmentation source must belong to one document"
                );
            }
            pythonSources.append(Py::asObject(source->getPyObject()));
        }
        PyObject* imported = PyImport_ImportModule("VibeCADMeshSegmentationGui");
        if (!imported) {
            throw Py::Exception();
        }
        Py::Module module(imported, true);
        module.callMemberFunction(
            "start_mesh_segmentation",
            Py::TupleN(
                pythonSources,
                Py::String(operation),
                Py::String(argumentsJson)
            )
        );
    }
    catch (const Py::Exception&) {
        Base::PyException error;
        throw Base::RuntimeError(error.what());
    }
}
