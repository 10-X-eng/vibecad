// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2004 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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

#include <Inventor/SoDB.h>
#include <Inventor/SoInput.h>
#include <Inventor/annex/ForeignFiles/SoSTLFileKit.h>
#include <Inventor/nodes/SoSeparator.h>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <ranges>

#include <QApplication>

#include <Base/Console.h>
#include <Base/Converter.h>
#include <Base/Interpreter.h>
#include <Base/Placement.h>
#include <Base/PyObjectBase.h>
#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObjectGroup.h>
#include <App/DocumentObjectPy.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Language/Translator.h>
#include <Gui/WidgetFactory.h>
#include <Mod/Mesh/App/FeatureMeshOperations.h>
#include <Mod/Mesh/App/FeatureMeshCurvature.h>

#include "DlgEvaluateMeshImp.h"
#include "DlgSettingsImportExportImp.h"
#include "DlgSettingsMeshView.h"
#include "PropertyEditorMesh.h"
#include "SoFCIndexedFaceSet.h"
#include "SoFCMeshObject.h"
#include "SoPolygon.h"
#include "ThumbnailExtension.h"
#include "ViewProvider.h"
#include "ViewProviderCurvature.h"
#include "ViewProviderDefects.h"
#include "ViewProviderMeshFaceSet.h"
#include "ViewProviderPython.h"
#include "ViewProviderTransform.h"
#include "ViewProviderTransformDemolding.h"
#include "Workbench.h"
#include "ParametricMeshFilter.h"
#include "CommandGuard.h"
#include "MeshSegmentationTools.h"


// use a different name to CreateCommand()
void CreateMeshCommands();

void loadMeshResource()
{
    // add resources and reloads the translators
    Q_INIT_RESOURCE(Mesh);
    Q_INIT_RESOURCE(Mesh_translation);
    Gui::Translator::instance()->refresh();
}

namespace MeshGui
{
class Module: public Py::ExtensionModule<Module>
{
public:
    Module()
        : Py::ExtensionModule<Module>("MeshGui")
    {
        add_varargs_method("convertToSTL", &Module::convertToSTL, "Convert a scene into an STL.");
        add_varargs_method(
            "publishStandaloneOutputs",
            &Module::publishStandaloneOutputs,
            "Publish exact same-transaction Mesh outputs as one standalone History operation."
        );
        add_varargs_method(
            "publishSourcePreservingOutputs",
            &Module::publishSourcePreservingOutputs,
            "Publish exact same-transaction outputs as one source-preserving History operation."
        );
        add_varargs_method(
            "publishReplacingOutputs",
            &Module::publishReplacingOutputs,
            "Publish paired same-transaction Mesh outputs as one replacement History operation."
        );
        add_varargs_method(
            "publishReplacingOperation",
            &Module::publishReplacingOperation,
            "Publish one linked operation which replaces several exact Mesh sources."
        );
        add_varargs_method(
            "ensureMeshesGroup",
            &Module::ensureMeshesGroup,
            "Place otherwise unowned Mesh objects in the document's Meshes folder."
        );
        add_varargs_method(
            "isNativeMeshInputActive",
            &Module::isNativeMeshInputActive,
            "Return whether an exact document object is usable at current Mesh History."
        );
        add_varargs_method(
            "detectCurvatureSegments",
            &Module::detectCurvatureSegments,
            "Detect exact facet segments using the native curvature algorithms."
        );
        add_varargs_method(
            "detectBestFitSegments",
            &Module::detectBestFitSegments,
            "Detect exact facet segments using native best-fit surfaces."
        );
        add_varargs_method(
            "detectPlanarSegments",
            &Module::detectPlanarSegments,
            "Detect exact planar facet segments using curvature and distance fitting."
        );
        add_varargs_method(
            "inspectNativeCurvature",
            &Module::inspectNativeCurvature,
            "Read bounded exact per-vertex values from a retained Mesh curvature result."
        );
        initialize("This module is the MeshGui module.");  // register with Python
    }

private:
    static Py::Object vectorValue(const Base::Vector3d& value)
    {
        Py::List result;
        result.append(Py::Float(value.x));
        result.append(Py::Float(value.y));
        result.append(Py::Float(value.z));
        return result;
    }

    static Mesh::Feature* exactMesh(PyObject* value)
    {
        if (!PyObject_TypeCheck(value, &App::DocumentObjectPy::Type)) {
            throw Py::TypeError("source must be a Mesh document object");
        }
        auto* object = static_cast<App::DocumentObjectPy*>(value)->getDocumentObjectPtr();
        auto* mesh = freecad_cast<Mesh::Feature*>(object);
        if (!mesh || !MeshGui::isNativeMeshInputActive(mesh)
            || mesh->Mesh.getValue().countFacets() == 0) {
            throw Py::ValueError("source must be one nonempty Mesh active in History");
        }
        return mesh;
    }

    static std::vector<float> floatSequence(
        PyObject* value,
        const char* error,
        std::size_t maximum = 7
    )
    {
        if (value == Py_None) {
            return {};
        }
        PyObject* sequence = PySequence_Fast(value, error);
        if (!sequence) {
            throw Py::Exception();
        }
        Py::Object owner(sequence, true);
        const Py_ssize_t count = PySequence_Fast_GET_SIZE(sequence);
        if (count < 1 || count > static_cast<Py_ssize_t>(maximum)) {
            throw Py::ValueError(error);
        }
        std::vector<float> result;
        result.reserve(static_cast<std::size_t>(count));
        for (Py_ssize_t index = 0; index < count; ++index) {
            const double number = PyFloat_AsDouble(PySequence_Fast_GET_ITEM(sequence, index));
            if (PyErr_Occurred() || !std::isfinite(number)) {
                throw Py::ValueError(error);
            }
            result.push_back(static_cast<float>(number));
        }
        return result;
    }

    static Py::Object segmentResult(const std::vector<MeshGui::DetectedMeshSegment>& segments)
    {
        Py::List result;
        for (const auto& segment : segments) {
            Py::Dict value;
            value.setItem("kind", Py::String(segment.kind));
            Py::List facets;
            for (const long facet : segment.facetIndices) {
                facets.append(Py::Long(facet));
            }
            value.setItem("facet_indices", facets);
            result.append(value);
        }
        return result;
    }

    Py::Object detectCurvatureSegments(const Py::Tuple& args)
    {
        PyObject* pythonSource {};
        PyObject* pythonRequests {};
        unsigned int smoothingSteps {};
        if (!PyArg_ParseTuple(args.ptr(), "OOI", &pythonSource, &pythonRequests, &smoothingSteps)) {
            throw Py::Exception();
        }
        Gui::requireMainThread("MeshGui.detectCurvatureSegments");
        auto* source = exactMesh(pythonSource);
        PyObject* sequence = PySequence_Fast(
            pythonRequests,
            "requests must contain 1 to 4 typed curvature requests"
        );
        if (!sequence) {
            throw Py::Exception();
        }
        Py::Object owner(sequence, true);
        const Py_ssize_t count = PySequence_Fast_GET_SIZE(sequence);
        if (count < 1 || count > 4 || smoothingSteps > 10000) {
            throw Py::ValueError("Curvature segmentation settings exceed their published limits");
        }
        std::vector<MeshGui::CurvatureSegmentRequest> requests;
        requests.reserve(static_cast<std::size_t>(count));
        for (Py_ssize_t index = 0; index < count; ++index) {
            PyObject* tuple = PySequence_Fast_GET_ITEM(sequence, index);
            if (!PyTuple_Check(tuple) || PyTuple_GET_SIZE(tuple) != 3) {
                throw Py::TypeError(
                    "Every curvature request must contain kind, minimum facets, and parameters"
                );
            }
            const char* kind = PyUnicode_AsUTF8(PyTuple_GET_ITEM(tuple, 0));
            const unsigned long minimum = PyLong_AsUnsignedLong(PyTuple_GET_ITEM(tuple, 1));
            if (!kind || PyErr_Occurred() || minimum < 1) {
                throw Py::ValueError(
                    "Every curvature request needs a valid kind and minimum facets"
                );
            }
            MeshGui::CurvatureSegmentKind parsedKind;
            if (strcmp(kind, "Plane") == 0) {
                parsedKind = MeshGui::CurvatureSegmentKind::Plane;
            }
            else if (strcmp(kind, "Cylinder") == 0) {
                parsedKind = MeshGui::CurvatureSegmentKind::Cylinder;
            }
            else if (strcmp(kind, "Sphere") == 0) {
                parsedKind = MeshGui::CurvatureSegmentKind::Sphere;
            }
            else if (strcmp(kind, "Freeform") == 0) {
                parsedKind = MeshGui::CurvatureSegmentKind::Freeform;
            }
            else {
                throw Py::ValueError(
                    "Curvature kind must be Plane, Cylinder, Sphere, or Freeform"
                );
            }
            requests.push_back({
                parsedKind,
                minimum,
                floatSequence(
                    PyTuple_GET_ITEM(tuple, 2),
                    "Invalid curvature parameters",
                    4
                ),
            });
        }
        return segmentResult(MeshGui::detectCurvatureSegments(
            source->Mesh.getValue(),
            requests,
            smoothingSteps
        ));
    }

    Py::Object detectBestFitSegments(const Py::Tuple& args)
    {
        PyObject* pythonSource {};
        PyObject* pythonRequests {};
        if (!PyArg_ParseTuple(args.ptr(), "OO", &pythonSource, &pythonRequests)) {
            throw Py::Exception();
        }
        Gui::requireMainThread("MeshGui.detectBestFitSegments");
        auto* source = exactMesh(pythonSource);
        PyObject* sequence = PySequence_Fast(
            pythonRequests,
            "requests must contain 1 to 3 typed best-fit requests"
        );
        if (!sequence) {
            throw Py::Exception();
        }
        Py::Object owner(sequence, true);
        const Py_ssize_t count = PySequence_Fast_GET_SIZE(sequence);
        if (count < 1 || count > 3) {
            throw Py::ValueError("Best-fit requests must contain 1 to 3 surfaces");
        }
        std::vector<MeshGui::BestFitSegmentRequest> requests;
        requests.reserve(static_cast<std::size_t>(count));
        for (Py_ssize_t index = 0; index < count; ++index) {
            PyObject* tuple = PySequence_Fast_GET_ITEM(sequence, index);
            if (!PyTuple_Check(tuple) || PyTuple_GET_SIZE(tuple) != 4) {
                throw Py::TypeError(
                    "Every best-fit request must contain kind, minimum facets, tolerance, and initial parameters"
                );
            }
            const char* kind = PyUnicode_AsUTF8(PyTuple_GET_ITEM(tuple, 0));
            const unsigned long minimum = PyLong_AsUnsignedLong(PyTuple_GET_ITEM(tuple, 1));
            const double tolerance = PyFloat_AsDouble(PyTuple_GET_ITEM(tuple, 2));
            if (!kind || PyErr_Occurred() || minimum < 1 || !std::isfinite(tolerance)
                || tolerance < 0.0) {
                throw Py::ValueError("Every best-fit request needs valid typed settings");
            }
            requests.push_back({
                kind,
                minimum,
                static_cast<float>(tolerance),
                floatSequence(
                    PyTuple_GET_ITEM(tuple, 3),
                    "Invalid best-fit initial parameters"
                ),
            });
        }
        return segmentResult(
            MeshGui::detectBestFitSegments(source->Mesh.getValue(), requests)
        );
    }

    Py::Object detectPlanarSegments(const Py::Tuple& args)
    {
        PyObject* pythonSource {};
        unsigned long minimum {};
        double curvatureTolerance {};
        double distanceTolerance {};
        unsigned int smoothingSteps {};
        if (!PyArg_ParseTuple(
                args.ptr(),
                "OkddI",
                &pythonSource,
                &minimum,
                &curvatureTolerance,
                &distanceTolerance,
                &smoothingSteps
            )) {
            throw Py::Exception();
        }
        Gui::requireMainThread("MeshGui.detectPlanarSegments");
        auto* source = exactMesh(pythonSource);
        if (minimum < 1 || !std::isfinite(curvatureTolerance)
            || !std::isfinite(distanceTolerance) || curvatureTolerance < 0.0
            || distanceTolerance < 0.0 || smoothingSteps > 10000) {
            throw Py::ValueError(
                "Planar segmentation settings exceed their published limits"
            );
        }
        return segmentResult(MeshGui::detectPlanarSegments(
            source->Mesh.getValue(),
            minimum,
            static_cast<float>(curvatureTolerance),
            static_cast<float>(distanceTolerance),
            smoothingSteps
        ));
    }

    Py::Object inspectNativeCurvature(const Py::Tuple& args)
    {
        PyObject* pythonCurvature {};
        PyObject* pythonIndices {};
        if (!PyArg_ParseTuple(args.ptr(), "OO", &pythonCurvature, &pythonIndices)) {
            throw Py::Exception();
        }
        Gui::requireMainThread("MeshGui.inspectNativeCurvature");
        if (!PyObject_TypeCheck(pythonCurvature, &App::DocumentObjectPy::Type)) {
            throw Py::TypeError("curvature must be a Mesh::Curvature document object");
        }
        auto* object = static_cast<App::DocumentObjectPy*>(
            pythonCurvature
        )->getDocumentObjectPtr();
        auto* curvature = freecad_cast<Mesh::Curvature*>(object);
        auto* source = curvature ? freecad_cast<Mesh::Feature*>(curvature->Source.getValue()) : nullptr;
        if (!curvature || !source || !MeshGui::isNativeMeshInputActive(curvature)
            || !MeshGui::isNativeMeshInputActive(source)
            || curvature->isError() || source->isError()) {
            throw Py::ValueError(
                "curvature must be a valid retained result active in current Mesh History"
            );
        }
        PyObject* sequence = PySequence_Fast(
            pythonIndices,
            "vertex indices must contain 1 to 32 unique non-negative integers"
        );
        if (!sequence) {
            throw Py::Exception();
        }
        Py::Object owner(sequence, true);
        const Py_ssize_t count = PySequence_Fast_GET_SIZE(sequence);
        if (count < 1 || count > 32) {
            throw Py::ValueError("vertex indices must contain 1 to 32 values");
        }
        std::vector<unsigned long> indices;
        indices.reserve(static_cast<std::size_t>(count));
        for (Py_ssize_t offset = 0; offset < count; ++offset) {
            const unsigned long index = PyLong_AsUnsignedLong(
                PySequence_Fast_GET_ITEM(sequence, offset)
            );
            if (PyErr_Occurred()) {
                throw Py::ValueError("every vertex index must be a non-negative integer");
            }
            indices.push_back(index);
        }
        auto sorted = indices;
        std::ranges::sort(sorted);
        if (std::adjacent_find(sorted.begin(), sorted.end()) != sorted.end()) {
            throw Py::ValueError("vertex indices must not contain duplicates");
        }
        const auto& values = curvature->CurvInfo.getValues();
        const Mesh::MeshObject& sourceMesh = source->Mesh.getValue();
        const auto& points = sourceMesh.getKernel().GetPoints();
        if (values.size() != points.size()) {
            throw Py::RuntimeError(
                "the retained curvature result does not match its source point topology"
            );
        }
        Base::Placement placement;
        placement.fromMatrix(sourceMesh.getTransform());
        Py::List result;
        for (const unsigned long index : indices) {
            if (index >= values.size()) {
                throw Py::IndexError("vertex index is outside the retained curvature result");
            }
            const auto& value = values[index];
            Base::Vector3d point = Base::convertTo<Base::Vector3d>(
                static_cast<const Base::Vector3f&>(points[index])
            );
            placement.multVec(point, point);
            Base::Vector3d maximumDirection = Base::convertTo<Base::Vector3d>(
                value.cMaxCurvDir
            );
            Base::Vector3d minimumDirection = Base::convertTo<Base::Vector3d>(
                value.cMinCurvDir
            );
            placement.getRotation().multVec(maximumDirection, maximumDirection);
            placement.getRotation().multVec(minimumDirection, minimumDirection);
            const double maximum = value.fMaxCurvature;
            const double minimum = value.fMinCurvature;
            Py::Dict item;
            item.setItem("vertex_index", Py::Long(index));
            item.setItem("point_mm", vectorValue(point));
            item.setItem("maximum_per_mm", Py::Float(maximum));
            item.setItem("minimum_per_mm", Py::Float(minimum));
            item.setItem("mean_per_mm", Py::Float(0.5 * (maximum + minimum)));
            item.setItem("gaussian_per_mm2", Py::Float(maximum * minimum));
            item.setItem(
                "absolute_per_mm",
                Py::Float(std::fabs(maximum) > std::fabs(minimum) ? maximum : minimum)
            );
            item.setItem("maximum_direction", vectorValue(maximumDirection));
            item.setItem("minimum_direction", vectorValue(minimumDirection));
            result.append(item);
        }
        return result;
    }

    Py::Object publishReplacingOperation(const Py::Tuple& args)
    {
        char* documentName {};
        PyObject* pythonSources {};
        PyObject* pythonOperation {};
        if (!PyArg_ParseTuple(
                args.ptr(),
                "sOO",
                &documentName,
                &pythonSources,
                &pythonOperation
            )) {
            throw Py::Exception();
        }
        Gui::requireMainThread("MeshGui.publishReplacingOperation");
        auto* document = App::GetApplication().getDocument(documentName);
        if (!document) {
            throw Py::RuntimeError("The exact Mesh document is no longer open.");
        }
        PyObject* sequence = PySequence_Fast(
            pythonSources,
            "sources must contain 1 to 32 document objects"
        );
        if (!sequence) {
            throw Py::Exception();
        }
        Py::Object owner(sequence, true);
        const Py_ssize_t count = PySequence_Fast_GET_SIZE(sequence);
        if (count < 1 || count > 32) {
            throw Py::ValueError("sources must contain 1 to 32 document objects");
        }
        std::vector<App::DocumentObject*> sources;
        sources.reserve(static_cast<std::size_t>(count));
        for (Py_ssize_t index = 0; index < count; ++index) {
            PyObject* value = PySequence_Fast_GET_ITEM(sequence, index);
            if (!PyObject_TypeCheck(value, &App::DocumentObjectPy::Type)) {
                throw Py::TypeError("every replacement source must be a document object");
            }
            auto* source = static_cast<App::DocumentObjectPy*>(value)->getDocumentObjectPtr();
            if (!source || source->getDocument() != document
                || !document->containsObject(source)) {
                throw Py::ValueError(
                    "every replacement source must be live in the exact document"
                );
            }
            sources.push_back(source);
        }
        if (!PyObject_TypeCheck(pythonOperation, &App::DocumentObjectPy::Type)) {
            throw Py::TypeError("operation must be a document object");
        }
        auto* operation = static_cast<App::DocumentObjectPy*>(
            pythonOperation
        )->getDocumentObjectPtr();
        if (!operation || operation->getDocument() != document
            || !document->containsObject(operation)) {
            throw Py::ValueError("operation must be live in the exact document");
        }
        MeshGui::createReplacingOperation(*document, sources, *operation);
        return Py::None();
    }

    Py::Object publishReplacingOutputs(const Py::Tuple& args)
    {
        char* documentName {};
        PyObject* pythonSources {};
        PyObject* pythonOutputs {};
        char* objectName {};
        char* label {};
        char* operationKind {};
        if (!PyArg_ParseTuple(
                args.ptr(),
                "sOOsss",
                &documentName,
                &pythonSources,
                &pythonOutputs,
                &objectName,
                &label,
                &operationKind
            )) {
            throw Py::Exception();
        }
        Gui::requireMainThread("MeshGui.publishReplacingOutputs");
        auto* document = App::GetApplication().getDocument(documentName);
        if (!document) {
            throw Py::RuntimeError("The exact Mesh document is no longer open.");
        }

        const auto exactObjects = [document](PyObject* pythonValues, const char* error) {
            PyObject* sequence = PySequence_Fast(pythonValues, error);
            if (!sequence) {
                throw Py::Exception();
            }
            Py::Object owner(sequence, true);
            const Py_ssize_t count = PySequence_Fast_GET_SIZE(sequence);
            if (count < 1 || count > 32) {
                throw Py::ValueError("Mesh replacement inputs must contain 1 to 32 objects");
            }
            std::vector<App::DocumentObject*> result;
            result.reserve(static_cast<std::size_t>(count));
            for (Py_ssize_t index = 0; index < count; ++index) {
                PyObject* value = PySequence_Fast_GET_ITEM(sequence, index);
                if (!PyObject_TypeCheck(value, &App::DocumentObjectPy::Type)) {
                    throw Py::TypeError("every Mesh replacement input must be a document object");
                }
                auto* object = static_cast<App::DocumentObjectPy*>(value)->getDocumentObjectPtr();
                if (!object || object->getDocument() != document
                    || !document->containsObject(object)) {
                    throw Py::ValueError(
                        "every Mesh replacement input must be live in the exact document"
                    );
                }
                result.push_back(object);
            }
            return result;
        };
        const auto sources = exactObjects(
            pythonSources,
            "sources must be a non-empty sequence of document objects"
        );
        const auto outputs = exactObjects(
            pythonOutputs,
            "outputs must be a non-empty sequence of document objects"
        );
        auto* group = MeshGui::createReplacingOutputGroup(
            *document,
            sources,
            outputs,
            objectName,
            label,
            operationKind
        );
        return group ? Py::asObject(group->getPyObject()) : Py::None();
    }

    Py::Object publishSourcePreservingOutputs(const Py::Tuple& args)
    {
        char* documentName {};
        PyObject* pythonSources {};
        PyObject* pythonOutputs {};
        char* objectName {};
        char* label {};
        char* operationKind {};
        if (!PyArg_ParseTuple(
                args.ptr(),
                "sOOsss",
                &documentName,
                &pythonSources,
                &pythonOutputs,
                &objectName,
                &label,
                &operationKind
            )) {
            throw Py::Exception();
        }
        Gui::requireMainThread("MeshGui.publishSourcePreservingOutputs");
        auto* document = App::GetApplication().getDocument(documentName);
        if (!document) {
            throw Py::RuntimeError("The exact Mesh document is no longer open.");
        }

        const auto exactObjects = [document](PyObject* pythonValues, const char* error) {
            PyObject* sequence = PySequence_Fast(pythonValues, error);
            if (!sequence) {
                throw Py::Exception();
            }
            Py::Object owner(sequence, true);
            const Py_ssize_t count = PySequence_Fast_GET_SIZE(sequence);
            if (count < 1 || count > 64) {
                throw Py::ValueError("Mesh History inputs must contain 1 to 64 objects");
            }
            std::vector<App::DocumentObject*> result;
            result.reserve(static_cast<std::size_t>(count));
            for (Py_ssize_t index = 0; index < count; ++index) {
                PyObject* value = PySequence_Fast_GET_ITEM(sequence, index);
                if (!PyObject_TypeCheck(value, &App::DocumentObjectPy::Type)) {
                    throw Py::TypeError("every Mesh History input must be a document object");
                }
                auto* object = static_cast<App::DocumentObjectPy*>(value)->getDocumentObjectPtr();
                if (!object || object->getDocument() != document
                    || !document->containsObject(object)) {
                    throw Py::ValueError(
                        "every Mesh History input must be live in the exact document"
                    );
                }
                result.push_back(object);
            }
            return result;
        };
        const auto sources = exactObjects(
            pythonSources,
            "sources must be a non-empty sequence of document objects"
        );
        const auto outputs = exactObjects(
            pythonOutputs,
            "outputs must be a non-empty sequence of document objects"
        );
        auto* group = MeshGui::createSourcePreservingOutputGroup(
            *document,
            sources,
            outputs,
            objectName,
            label,
            operationKind
        );
        return group ? Py::asObject(group->getPyObject()) : Py::None();
    }

    Py::Object isNativeMeshInputActive(const Py::Tuple& args)
    {
        PyObject* pythonObject {};
        if (!PyArg_ParseTuple(args.ptr(), "O", &pythonObject)) {
            throw Py::Exception();
        }
        Gui::requireMainThread("MeshGui.isNativeMeshInputActive");
        if (!PyObject_TypeCheck(pythonObject, &App::DocumentObjectPy::Type)) {
            throw Py::TypeError("object must be a document object");
        }
        auto* object = static_cast<App::DocumentObjectPy*>(pythonObject)->getDocumentObjectPtr();
        return Py::Boolean(MeshGui::isNativeMeshInputActive(object));
    }

    Py::Object ensureMeshesGroup(const Py::Tuple& args)
    {
        char* documentName {};
        if (!PyArg_ParseTuple(args.ptr(), "s", &documentName)) {
            throw Py::Exception();
        }
        Gui::requireMainThread("MeshGui.ensureMeshesGroup");
        auto* document = App::GetApplication().getDocument(documentName);
        if (!document) {
            throw Py::RuntimeError("The exact Mesh document is no longer open.");
        }
        auto* group = MeshGui::ensureMeshesGroup(*document);
        return group ? Py::asObject(group->getPyObject()) : Py::None();
    }

    Py::Object publishStandaloneOutputs(const Py::Tuple& args)
    {
        char* documentName {};
        PyObject* pythonOutputs {};
        PyObject* pythonExternalInputs {};
        char* objectName {};
        char* label {};
        char* operationKind {};
        if (!PyArg_ParseTuple(
                args.ptr(),
                "sOOsss",
                &documentName,
                &pythonOutputs,
                &pythonExternalInputs,
                &objectName,
                &label,
                &operationKind
            )) {
            throw Py::Exception();
        }
        Gui::requireMainThread("MeshGui.publishStandaloneOutputs");
        auto* document = App::GetApplication().getDocument(documentName);
        if (!document) {
            throw Py::RuntimeError("The exact Mesh document is no longer open.");
        }

        PyObject* outputSequence = PySequence_Fast(
            pythonOutputs,
            "outputs must be a non-empty sequence of document objects"
        );
        if (!outputSequence) {
            throw Py::Exception();
        }
        Py::Object outputOwner(outputSequence, true);
        const Py_ssize_t outputCount = PySequence_Fast_GET_SIZE(outputSequence);
        if (outputCount < 1 || outputCount > 64) {
            throw Py::ValueError("outputs must contain 1 to 64 exact document objects");
        }
        std::vector<App::DocumentObject*> outputs;
        outputs.reserve(static_cast<std::size_t>(outputCount));
        for (Py_ssize_t index = 0; index < outputCount; ++index) {
            PyObject* value = PySequence_Fast_GET_ITEM(outputSequence, index);
            if (!PyObject_TypeCheck(value, &App::DocumentObjectPy::Type)) {
                throw Py::TypeError("every output must be a document object");
            }
            auto* output = static_cast<App::DocumentObjectPy*>(value)->getDocumentObjectPtr();
            if (!output || output->getDocument() != document || !document->containsObject(output)) {
                throw Py::ValueError("every output must be live in the exact Mesh document");
            }
            outputs.push_back(output);
        }

        PyObject* inputSequence = PySequence_Fast(
            pythonExternalInputs,
            "external_inputs must be a sequence of portable basenames"
        );
        if (!inputSequence) {
            throw Py::Exception();
        }
        Py::Object inputOwner(inputSequence, true);
        const Py_ssize_t inputCount = PySequence_Fast_GET_SIZE(inputSequence);
        if (inputCount < 1 || inputCount > 64) {
            throw Py::ValueError("external_inputs must contain 1 to 64 basenames");
        }
        std::vector<std::string> externalInputs;
        externalInputs.reserve(static_cast<std::size_t>(inputCount));
        for (Py_ssize_t index = 0; index < inputCount; ++index) {
            PyObject* value = PySequence_Fast_GET_ITEM(inputSequence, index);
            if (!PyUnicode_Check(value)) {
                throw Py::TypeError("every external input identity must be text");
            }
            const char* text = PyUnicode_AsUTF8(value);
            const std::string basename = text ? text : "";
            if (basename.empty() || basename == "." || basename == ".."
                || basename.find('/') != std::string::npos
                || basename.find('\\') != std::string::npos) {
                throw Py::ValueError("external input identities must be portable basenames");
            }
            externalInputs.push_back(basename);
        }

        auto* group = MeshGui::createStandaloneOutputGroup(
            *document,
            outputs,
            externalInputs,
            objectName,
            label,
            operationKind
        );
        return group ? Py::asObject(group->getPyObject()) : Py::None();
    }

    Py::Object convertToSTL(const Py::Tuple& args)
    {
        char* inname {};
        char* outname {};
        if (!PyArg_ParseTuple(args.ptr(), "etet", "utf-8", &inname, "utf-8", &outname)) {
            throw Py::Exception();
        }
        std::string inputName = std::string(inname);
        PyMem_Free(inname);
        std::string outputName = std::string(outname);
        PyMem_Free(outname);

        bool ok = false;
        SoInput in;
        if (in.openFile(inputName.c_str())) {
            SoSeparator* node = SoDB::readAll(&in);
            if (node) {
                node->ref();
                SoSTLFileKit* stlKit = new SoSTLFileKit();
                stlKit->ref();
                ok = stlKit->readScene(node);
                stlKit->writeFile(outputName.c_str());
                stlKit->unref();
                node->unref();
            }
        }

        return Py::Boolean(ok);  // NOLINT
    }
};

PyObject* initModule()
{
    return Base::Interpreter().addModule(new Module);
}

}  // namespace MeshGui

/* Python entry */
PyMOD_INIT_FUNC(MeshGui)
{
    if (!Gui::Application::Instance) {
        PyErr_SetString(PyExc_ImportError, "Cannot load Gui module in console application.");
        PyMOD_Return(nullptr);
    }

    // load dependent module
    try {
        Base::Interpreter().loadModule("Mesh");
    }
    catch (const Base::Exception& e) {
        PyErr_SetString(PyExc_ImportError, e.what());
        PyMOD_Return(nullptr);
    }
    PyObject* mod = MeshGui::initModule();
    Base::Console().log("Loading GUI of Mesh module… done\n");

    // instantiating the commands
    CreateMeshCommands();
    if (qApp) {
        (void)new MeshGui::CleanupHandler;
    }

    // NOLINTBEGIN
    // try to instantiate flat-mesh commands
    try {
        Base::Interpreter().runString("import MeshFlatteningCommand");
    }
    catch (Base::PyException& err) {
        err.reportException();
    }

    // register preferences pages
    (void)new Gui::PrefPageProducer<MeshGui::DlgSettingsMeshView>(
        QT_TRANSLATE_NOOP("QObject", "Display")
    );
    (void)new Gui::PrefPageProducer<MeshGui::DlgSettingsImportExport>(
        QT_TRANSLATE_NOOP("QObject", "Import-Export")
    );

    Mesh::Extension3MFFactory::addProducer(new MeshGui::ThumbnailExtensionProducer);
    // NOLINTEND

    // clang-format off
    MeshGui::SoFCMeshObjectElement              ::initClass();
    MeshGui::SoSFMeshObject                     ::initClass();
    MeshGui::SoFCMeshObjectNode                 ::initClass();
    MeshGui::SoFCMeshObjectShape                ::initClass();
    MeshGui::SoFCMeshSegmentShape               ::initClass();
    MeshGui::SoFCMeshObjectBoundary             ::initClass();
    MeshGui::SoFCMaterialEngine                 ::initClass();
    MeshGui::SoFCIndexedFaceSet                 ::initClass();
    MeshGui::SoFCMeshPickNode                   ::initClass();
    MeshGui::SoFCMeshGridNode                   ::initClass();
    MeshGui::SoPolygon                          ::initClass();
    MeshGui::PropertyMeshKernelItem             ::init();
    MeshGui::ViewProviderMesh                   ::init();
    MeshGui::ViewProviderMeshOutputGroup        ::init();
    MeshGui::ViewProviderMeshObject             ::init();
    MeshGui::ViewProviderIndexedFaceSet         ::init();
    MeshGui::ViewProviderMeshFaceSet            ::init();
    MeshGui::ViewProviderPython                 ::init();
    MeshGui::ViewProviderExport                 ::init();
    MeshGui::ViewProviderMeshCurvature          ::init();
    MeshGui::ViewProviderMeshTransform          ::init();
    MeshGui::ViewProviderMeshTransformDemolding ::init();
    MeshGui::ViewProviderMeshDefects            ::init();
    MeshGui::ViewProviderMeshOrientation        ::init();
    MeshGui::ViewProviderMeshNonManifolds       ::init();
    MeshGui::ViewProviderMeshNonManifoldPoints  ::init();
    MeshGui::ViewProviderMeshDuplicatedFaces    ::init();
    MeshGui::ViewProviderMeshDuplicatedPoints   ::init();
    MeshGui::ViewProviderMeshDegenerations      ::init();
    MeshGui::ViewProviderMeshIndices            ::init();
    MeshGui::ViewProviderMeshSelfIntersections  ::init();
    MeshGui::ViewProviderMeshFolds              ::init();
    MeshGui::Workbench                          ::init();
    Gui::ViewProviderBuilder::add(
        Mesh::PropertyMeshKernel::getClassTypeId(),
        MeshGui::ViewProviderMeshFaceSet::getClassTypeId());

    // add resources and reloads the translators
    loadMeshResource();
    // clang-format on

    PyMOD_Return(mod);
}
