// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2010 Jürgen Riegel <juergen.riegel@web.de>              *
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

#include <algorithm>
#include <cmath>
#include <limits>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

#include <boost/uuid/uuid_io.hpp>

#include <Geom_TrimmedCurve.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <BRepCheck_ListIteratorOfListOfStatus.hxx>
#include <BRepCheck_Result.hxx>
#include <BRep_Tool.hxx>
#include <TopExp.hxx>
#include <TopoDS.hxx>

#include <App/Document.h>
#include <Base/AxisPy.h>
#include <Base/QuantityPy.h>
#include <Base/Tools.h>
#include <Base/VectorPy.h>
#include <Mod/Part/App/Geometry.h>
#include <Mod/Part/App/LinePy.h>

#include "PythonConverter.h"

// inclusion of the generated files (generated out of SketchObjectSFPy.xml)
#include "SketchObjectPy.h"

#include "SketchObjectPy.cpp"

// other python types
#include "ConstraintPy.h"
#include "ExternalGeometryFacade.h"
#include "GeometryFacadePy.h"
#include "SketchAnalysis.h"


using namespace Sketcher;

namespace
{
constexpr const char* mutationStateCapsuleName = "Sketcher.SketchMutationState";

struct SketchMutationState
{
    // Property Copy/Paste is the same typed state mechanism used by document
    // transactions; keeping it opaque avoids a lossy Python serialization.
    explicit SketchMutationState(SketchObject* sketch)
        : owner(sketch)
        , document(sketch ? sketch->getDocument() : nullptr)
        , transactionId(document ? document->getBookedTransactionID() : App::NullTransaction)
        , objectName(sketch && sketch->getNameInDocument() ? sketch->getNameInDocument() : "")
        , expressionEngine(sketch->ExpressionEngine.Copy())
        , constraints(sketch->Constraints.Copy())
        , geometry(sketch->Geometry.Copy())
        , externalTypes(sketch->ExternalTypes.Copy())
        , externalGeometry(sketch->ExternalGeometry.Copy())
        , externalGeo(sketch->ExternalGeo.Copy())
        , exports(sketch->Exports.Copy())
    {}

    SketchObject* owner;
    App::Document* document;
    int transactionId;
    std::string objectName;
    std::unique_ptr<App::Property> expressionEngine;
    std::unique_ptr<App::Property> constraints;
    std::unique_ptr<App::Property> geometry;
    std::unique_ptr<App::Property> externalTypes;
    std::unique_ptr<App::Property> externalGeometry;
    std::unique_ptr<App::Property> externalGeo;
    std::unique_ptr<App::Property> exports;
    bool restored = false;
};

void deleteMutationStateCapsule(PyObject* capsule)
{
    auto* state = static_cast<SketchMutationState*>(
        PyCapsule_GetPointer(capsule, mutationStateCapsuleName)
    );
    if (!state) {
        PyErr_Clear();
        return;
    }
    delete state;
}

struct SketchMutationSnapshot
{
    std::vector<std::string> geometryTags;
    std::vector<std::string> constraintTags;
};

SketchMutationSnapshot mutationSnapshot(const SketchObject* sketch)
{
    SketchMutationSnapshot snapshot;
    const int geometryCount = sketch->Geometry.getSize();
    snapshot.geometryTags.reserve(static_cast<std::size_t>(geometryCount));
    for (int index = 0; index < geometryCount; ++index) {
        const auto facade = sketch->getGeometryFacade(index);
        snapshot.geometryTags.push_back(
            facade ? boost::uuids::to_string(facade->getTag()) : std::string());
    }
    const auto& constraints = sketch->Constraints.getValues();
    snapshot.constraintTags.reserve(constraints.size());
    for (const auto* constraint : constraints) {
        snapshot.constraintTags.push_back(
            constraint ? boost::uuids::to_string(constraint->getTag()) : std::string());
    }
    return snapshot;
}

PyObject* mutationCollectionMap(const std::vector<std::string>& before,
                                const std::vector<std::string>& after)
{
    PyObject* result = PyDict_New();
    PyObject* oldToNew = PyDict_New();
    PyObject* deleted = PyList_New(0);
    PyObject* created = PyList_New(0);
    std::vector<bool> matched(after.size(), false);

    for (std::size_t oldIndex = 0; oldIndex < before.size(); ++oldIndex) {
        std::size_t newIndex = after.size();
        for (std::size_t candidate = 0; candidate < after.size(); ++candidate) {
            if (!matched[candidate] && before[oldIndex] == after[candidate]) {
                newIndex = candidate;
                matched[candidate] = true;
                break;
            }
        }
        if (newIndex < after.size()) {
            PyObject* key = PyUnicode_FromFormat("%zu", oldIndex);
            PyObject* value = PyLong_FromSize_t(newIndex);
            PyDict_SetItem(oldToNew, key, value);
            Py_DECREF(key);
            Py_DECREF(value);
        }
        else {
            PyObject* item = PyDict_New();
            PyObject* index = PyLong_FromSize_t(oldIndex);
            PyObject* tag = PyUnicode_FromString(before[oldIndex].c_str());
            PyDict_SetItemString(item, "index", index);
            PyDict_SetItemString(item, "tag", tag);
            Py_DECREF(index);
            Py_DECREF(tag);
            PyList_Append(deleted, item);
            Py_DECREF(item);
        }
    }
    for (std::size_t newIndex = 0; newIndex < after.size(); ++newIndex) {
        if (matched[newIndex]) {
            continue;
        }
        PyObject* item = PyDict_New();
        PyObject* index = PyLong_FromSize_t(newIndex);
        PyObject* tag = PyUnicode_FromString(after[newIndex].c_str());
        PyDict_SetItemString(item, "index", index);
        PyDict_SetItemString(item, "tag", tag);
        Py_DECREF(index);
        Py_DECREF(tag);
        PyList_Append(created, item);
        Py_DECREF(item);
    }

    PyObject* identity = PyUnicode_FromString("native_tag");
    PyDict_SetItemString(result, "identity", identity);
    Py_DECREF(identity);
    PyDict_SetItemString(result, "old_to_new", oldToNew);
    PyDict_SetItemString(result, "deleted", deleted);
    PyDict_SetItemString(result, "created", created);
    Py_DECREF(oldToNew);
    Py_DECREF(deleted);
    Py_DECREF(created);
    return result;
}

PyObject* mutationResult(const SketchMutationSnapshot& before, const SketchObject* sketch)
{
    const auto after = mutationSnapshot(sketch);
    PyObject* result = PyDict_New();
    PyObject* geometry = mutationCollectionMap(before.geometryTags, after.geometryTags);
    PyObject* constraints = mutationCollectionMap(before.constraintTags, after.constraintTags);
    PyDict_SetItemString(result, "geometry", geometry);
    PyDict_SetItemString(result, "constraints", constraints);
    Py_DECREF(geometry);
    Py_DECREF(constraints);
    return result;
}

bool proposedConstraintsFromPython(PyObject* object, std::vector<Constraint*>& values)
{
    if (PyObject_TypeCheck(object, &(Sketcher::ConstraintPy::Type))) {
        values.push_back(
            static_cast<Sketcher::ConstraintPy*>(object)->getConstraintPtr());
    }
    else if (PyList_Check(object) || PyTuple_Check(object)) {
        Py::Sequence sequence(object);
        for (Py::Sequence::iterator it = sequence.begin(); it != sequence.end(); ++it) {
            if (!PyObject_TypeCheck((*it).ptr(), &(ConstraintPy::Type))) {
                PyErr_SetString(PyExc_TypeError,
                                "Every item must be a Sketcher.Constraint.");
                return false;
            }
            values.push_back(
                static_cast<ConstraintPy*>((*it).ptr())->getConstraintPtr());
        }
    }
    else {
        PyErr_SetString(PyExc_TypeError,
                        "constraints must be a Sketcher.Constraint or sequence.");
        return false;
    }
    if (values.empty()) {
        PyErr_SetString(PyExc_ValueError, "constraints must not be empty.");
        return false;
    }
    return true;
}

PyObject* solverDiagnosticResult(SketchObject* sketch, int degreesOfFreedom)
{
    const int solverStatus = sketch->getLastSolverStatus();
    const auto conflicting = sketch->getLastConflicting();
    const auto redundant = sketch->getLastRedundant();
    const auto partiallyRedundant = sketch->getLastPartiallyRedundant();
    const auto malformed = sketch->getLastMalformedConstraints();
    const bool accepted = degreesOfFreedom >= 0 && conflicting.empty() && redundant.empty()
        && partiallyRedundant.empty() && malformed.empty();

    // Restore the live solver diagnostics after collecting the hypothetical result.
    sketch->setUpSketch();

    auto makeIndexList = [](const std::vector<int>& indices) {
        PyObject* result = PyList_New(static_cast<Py_ssize_t>(indices.size()));
        for (std::size_t index = 0; index < indices.size(); ++index) {
            PyList_SET_ITEM(result,
                            static_cast<Py_ssize_t>(index),
                            PyLong_FromLong(indices[index]));
        }
        return result;
    };

    PyObject* result = PyDict_New();
    PyObject* acceptedValue = PyBool_FromLong(accepted);
    PyObject* dofValue = PyLong_FromLong(degreesOfFreedom);
    PyObject* statusValue = PyLong_FromLong(solverStatus);
    PyObject* conflictingValue = makeIndexList(conflicting);
    PyObject* redundantValue = makeIndexList(redundant);
    PyObject* partialValue = makeIndexList(partiallyRedundant);
    PyObject* malformedValue = makeIndexList(malformed);
    PyDict_SetItemString(result, "accepted", acceptedValue);
    PyDict_SetItemString(result, "degrees_of_freedom", dofValue);
    PyDict_SetItemString(result, "solver_status", statusValue);
    PyDict_SetItemString(result, "conflicting_constraint_indices", conflictingValue);
    PyDict_SetItemString(result, "redundant_constraint_indices", redundantValue);
    PyDict_SetItemString(result, "partially_redundant_constraint_indices", partialValue);
    PyDict_SetItemString(result, "malformed_constraint_indices", malformedValue);
    Py_DECREF(acceptedValue);
    Py_DECREF(dofValue);
    Py_DECREF(statusValue);
    Py_DECREF(conflictingValue);
    Py_DECREF(redundantValue);
    Py_DECREF(partialValue);
    Py_DECREF(malformedValue);
    return result;
}

PyObject* constraintDiagnosticResult(SketchObject* sketch,
                                     int firstProposedIndex,
                                     std::size_t proposedCount,
                                     int degreesOfFreedom)
{
    PyObject* result = solverDiagnosticResult(sketch, degreesOfFreedom);
    PyObject* firstIndexValue = PyLong_FromLong(firstProposedIndex);
    PyObject* proposedCountValue = PyLong_FromSize_t(proposedCount);
    PyDict_SetItemString(result, "first_proposed_constraint_index", firstIndexValue);
    PyDict_SetItemString(result, "proposed_constraint_count", proposedCountValue);
    Py_DECREF(firstIndexValue);
    Py_DECREF(proposedCountValue);
    return result;
}

void addDiagnosticSketchState(PyObject* result, SketchObject* diagnostic)
{
    PyObject* geometryCountValue = PyLong_FromLong(diagnostic->Geometry.getSize());
    PyObject* constraintCountValue = PyLong_FromLong(diagnostic->Constraints.getSize());
    PyObject* geometryValues = PyList_New(diagnostic->Geometry.getSize());
    PyObject* metadataValues = PyList_New(diagnostic->Geometry.getSize());
    for (int index = 0; index < diagnostic->Geometry.getSize(); ++index) {
        auto* geometry = diagnostic->Geometry.getValues()[index];
        auto facade = GeometryFacade::getFacade(geometry);
        PyList_SET_ITEM(geometryValues, index, geometry->getPyObject());

        PyObject* metadata = PyDict_New();
        PyObject* id = PyLong_FromLong(facade->getId());
        PyObject* construction = PyBool_FromLong(facade->getConstruction());
        PyObject* blocked = PyBool_FromLong(facade->getBlocked());
        const auto internalType = static_cast<std::size_t>(facade->getInternalType());
        const char* internalTypeName = internalType < SketchGeometryExtension::internaltype2str.size()
            ? SketchGeometryExtension::internaltype2str[internalType]
            : "Unknown";
        PyObject* internal = PyUnicode_FromString(internalTypeName);
        PyObject* layer = PyLong_FromLong(facade->getGeometryLayerId());
        PyDict_SetItemString(metadata, "Id", id);
        PyDict_SetItemString(metadata, "Construction", construction);
        PyDict_SetItemString(metadata, "Blocked", blocked);
        PyDict_SetItemString(metadata, "InternalType", internal);
        PyDict_SetItemString(metadata, "GeometryLayerId", layer);
        Py_DECREF(id);
        Py_DECREF(construction);
        Py_DECREF(blocked);
        Py_DECREF(internal);
        Py_DECREF(layer);
        PyList_SET_ITEM(metadataValues, index, metadata);
    }

    PyObject* constraintValues = PyList_New(diagnostic->Constraints.getSize());
    const auto& constraints = diagnostic->Constraints.getValues();
    for (int index = 0; index < diagnostic->Constraints.getSize(); ++index) {
        PyList_SET_ITEM(
            constraintValues,
            index,
            new ConstraintPy(constraints[index]->clone())
        );
    }

    PyDict_SetItemString(result, "geometry_count", geometryCountValue);
    PyDict_SetItemString(result, "constraint_count", constraintCountValue);
    PyDict_SetItemString(result, "geometry", geometryValues);
    PyDict_SetItemString(result, "geometry_metadata", metadataValues);
    PyDict_SetItemString(result, "constraints", constraintValues);
    Py_DECREF(geometryCountValue);
    Py_DECREF(constraintCountValue);
    Py_DECREF(geometryValues);
    Py_DECREF(metadataValues);
    Py_DECREF(constraintValues);
}

PyObject* filletChamferDiagnosticResult(std::unique_ptr<SketchObject> diagnostic,
                                        const std::vector<int>& inputGeometryIndices,
                                        const char* form,
                                        int originalGeometryCount,
                                        bool trimmed,
                                        bool chamfer)
{
    const char* operation = chamfer ? "Chamfer" : "Fillet";
    const int minimumAddedGeometry = chamfer ? 2 : 1;
    if (!diagnostic || inputGeometryIndices.size() != 2 || !form
        || originalGeometryCount < 0
        || diagnostic->Geometry.getSize() < originalGeometryCount + minimumAddedGeometry) {
        const std::string message = std::string(operation) + " diagnosis produced an invalid state.";
        PyErr_SetString(PyExc_RuntimeError, message.c_str());
        return nullptr;
    }

    const int supportArcIndex = originalGeometryCount;
    const auto* supportArcGeometry = diagnostic->getGeometry(supportArcIndex);
    if (!supportArcGeometry || !supportArcGeometry->is<Part::GeomArcOfCircle>()) {
        const std::string message = std::string(operation)
            + " diagnosis did not produce one circular support arc.";
        PyErr_SetString(PyExc_RuntimeError, message.c_str());
        return nullptr;
    }
    const auto* supportArc = static_cast<const Part::GeomArcOfCircle*>(supportArcGeometry);

    const int addedGeometry = diagnostic->Geometry.getSize() - originalGeometryCount;
    const bool preservedCorner = addedGeometry == minimumAddedGeometry + 1;
    if (addedGeometry != minimumAddedGeometry && !preservedCorner) {
        const std::string message = std::string(operation) + " diagnosis added unexpected geometry.";
        PyErr_SetString(PyExc_RuntimeError, message.c_str());
        return nullptr;
    }
    const int cornerIndex = preservedCorner ? originalGeometryCount + 1 : -1;
    const int chamferIndex = chamfer
        ? originalGeometryCount + 1 + (preservedCorner ? 1 : 0)
        : -1;
    if (preservedCorner) {
        const auto* cornerGeometry = diagnostic->getGeometry(cornerIndex);
        if (!cornerGeometry || !cornerGeometry->is<Part::GeomPoint>()) {
            const std::string message = std::string(operation)
                + " diagnosis did not produce the preserved corner point.";
            PyErr_SetString(PyExc_RuntimeError, message.c_str());
            return nullptr;
        }
    }
    if (chamfer) {
        const auto* chamferGeometry = diagnostic->getGeometry(chamferIndex);
        if (!chamferGeometry || !chamferGeometry->is<Part::GeomLineSegment>()) {
            PyErr_SetString(PyExc_RuntimeError,
                            "Chamfer diagnosis did not produce one chamfer line.");
            return nullptr;
        }
    }

    PyObject* result = solverDiagnosticResult(diagnostic.get(), diagnostic->getLastDoF());
    PyObject* formValue = PyUnicode_FromString(form);
    PyObject* inputValues = PyList_New(2);
    PyObject* supportArcIndexValue = PyLong_FromLong(supportArcIndex);
    PyObject* chamferIndexValue = chamfer ? PyLong_FromLong(chamferIndex) : nullptr;
    PyObject* radiusValue = PyFloat_FromDouble(supportArc->getRadius());
    PyObject* trimmedValue = PyBool_FromLong(trimmed);
    const int resultGeometryIndex = chamfer ? chamferIndex : supportArcIndex;
    PyObject* constructionValue = PyBool_FromLong(
        GeometryFacade::getConstruction(diagnostic->getGeometry(resultGeometryIndex))
    );
    PyList_SET_ITEM(inputValues, 0, PyLong_FromLong(inputGeometryIndices[0]));
    PyList_SET_ITEM(inputValues, 1, PyLong_FromLong(inputGeometryIndices[1]));

    PyObject* cornerIndexValue = preservedCorner ? PyLong_FromLong(cornerIndex) : Py_None;
    if (!preservedCorner) {
        Py_INCREF(cornerIndexValue);
    }

    PyDict_SetItemString(result, "form", formValue);
    PyDict_SetItemString(result, "input_geometry_indices", inputValues);
    PyDict_SetItemString(result,
                         chamfer ? "support_arc_geometry_index" : "fillet_geometry_index",
                         supportArcIndexValue);
    if (chamfer) {
        PyDict_SetItemString(result, "chamfer_geometry_index", chamferIndexValue);
    }
    PyDict_SetItemString(result, "corner_geometry_index", cornerIndexValue);
    PyDict_SetItemString(result, "radius_mm", radiusValue);
    PyDict_SetItemString(result, "trimmed", trimmedValue);
    PyDict_SetItemString(result, "construction", constructionValue);
    addDiagnosticSketchState(result, diagnostic.get());
    Py_DECREF(formValue);
    Py_DECREF(inputValues);
    Py_DECREF(supportArcIndexValue);
    Py_XDECREF(chamferIndexValue);
    Py_DECREF(cornerIndexValue);
    Py_DECREF(radiusValue);
    Py_DECREF(trimmedValue);
    Py_DECREF(constructionValue);
    return result;
}

PyObject* filletDiagnosticResult(std::unique_ptr<SketchObject> diagnostic,
                                 const std::vector<int>& inputGeometryIndices,
                                 const char* form,
                                 int originalGeometryCount,
                                 bool trimmed)
{
    return filletChamferDiagnosticResult(
        std::move(diagnostic),
        inputGeometryIndices,
        form,
        originalGeometryCount,
        trimmed,
        false
    );
}

PyObject* chamferDiagnosticResult(std::unique_ptr<SketchObject> diagnostic,
                                  const std::vector<int>& inputGeometryIndices,
                                  const char* form,
                                  int originalGeometryCount,
                                  bool trimmed)
{
    return filletChamferDiagnosticResult(
        std::move(diagnostic),
        inputGeometryIndices,
        form,
        originalGeometryCount,
        trimmed,
        true
    );
}

PyObject* curvePointMutationDiagnosticResult(const char* operation,
                                             std::unique_ptr<SketchObject> diagnostic,
                                             const SketchMutationSnapshot& before,
                                             int inputGeometryIndex,
                                             const Base::Vector3d& referencePoint)
{
    if (!diagnostic || inputGeometryIndex < 0
        || static_cast<std::size_t>(inputGeometryIndex) >= before.geometryTags.size()) {
        const std::string message = std::string(operation)
            + " diagnosis produced an invalid state.";
        PyErr_SetString(PyExc_RuntimeError, message.c_str());
        return nullptr;
    }
    PyObject* result = solverDiagnosticResult(diagnostic.get(), diagnostic->getLastDoF());
    PyObject* inputIndexValue = PyLong_FromLong(inputGeometryIndex);
    PyObject* pointValue = PyList_New(2);
    PyList_SET_ITEM(pointValue, 0, PyFloat_FromDouble(referencePoint.x));
    PyList_SET_ITEM(pointValue, 1, PyFloat_FromDouble(referencePoint.y));
    const int externalGeometryCount = std::max(0, diagnostic->ExternalGeo.getSize() - 2);
    PyObject* externalCountValue = PyLong_FromLong(externalGeometryCount);
    PyObject* receiptValue = mutationResult(before, diagnostic.get());
    PyDict_SetItemString(result, "input_geometry_index", inputIndexValue);
    PyDict_SetItemString(result, "reference_point_mm", pointValue);
    PyDict_SetItemString(result, "external_geometry_count", externalCountValue);
    PyDict_SetItemString(result, "mutation_receipt", receiptValue);
    addDiagnosticSketchState(result, diagnostic.get());
    Py_DECREF(inputIndexValue);
    Py_DECREF(pointValue);
    Py_DECREF(externalCountValue);
    Py_DECREF(receiptValue);
    return result;
}

int joinEndpointContinuity(const SketchObject* sketch,
                           int firstGeometry,
                           PointPos firstEndpoint,
                           int secondGeometry,
                           PointPos secondEndpoint)
{
    for (const auto* constraint : sketch->Constraints.getValues()) {
        if (constraint && constraint->Type == ConstraintType::Tangent
            && ((constraint->First == firstGeometry
                 && constraint->FirstPos == firstEndpoint
                 && constraint->Second == secondGeometry
                 && constraint->SecondPos == secondEndpoint)
                || (constraint->First == secondGeometry
                    && constraint->FirstPos == secondEndpoint
                    && constraint->Second == firstGeometry
                    && constraint->SecondPos == firstEndpoint))) {
            return 1;
        }
    }
    return 0;
}

PyObject* joinCurvesDiagnosticResult(std::unique_ptr<SketchObject> diagnostic,
                                     const SketchMutationSnapshot& before,
                                     int firstGeometry,
                                     PointPos firstEndpoint,
                                     int secondGeometry,
                                     PointPos secondEndpoint,
                                     int continuity)
{
    if (!diagnostic || firstGeometry < 0 || secondGeometry < 0
        || firstGeometry == secondGeometry) {
        PyErr_SetString(PyExc_RuntimeError,
                        "Join Curves diagnosis produced an invalid state.");
        return nullptr;
    }
    PyObject* result = solverDiagnosticResult(diagnostic.get(), diagnostic->getLastDoF());
    PyObject* firstGeometryValue = PyLong_FromLong(firstGeometry);
    PyObject* firstEndpointValue = PyLong_FromLong(static_cast<int>(firstEndpoint));
    PyObject* secondGeometryValue = PyLong_FromLong(secondGeometry);
    PyObject* secondEndpointValue = PyLong_FromLong(static_cast<int>(secondEndpoint));
    PyObject* continuityValue = PyLong_FromLong(continuity);
    const int externalGeometryCount = std::max(0, diagnostic->ExternalGeo.getSize() - 2);
    PyObject* externalCountValue = PyLong_FromLong(externalGeometryCount);
    PyObject* receiptValue = mutationResult(before, diagnostic.get());
    PyDict_SetItemString(result, "first_geometry_index", firstGeometryValue);
    PyDict_SetItemString(result, "first_endpoint", firstEndpointValue);
    PyDict_SetItemString(result, "second_geometry_index", secondGeometryValue);
    PyDict_SetItemString(result, "second_endpoint", secondEndpointValue);
    PyDict_SetItemString(result, "continuity", continuityValue);
    PyDict_SetItemString(result, "external_geometry_count", externalCountValue);
    PyDict_SetItemString(result, "mutation_receipt", receiptValue);
    addDiagnosticSketchState(result, diagnostic.get());
    Py_DECREF(firstGeometryValue);
    Py_DECREF(firstEndpointValue);
    Py_DECREF(secondGeometryValue);
    Py_DECREF(secondEndpointValue);
    Py_DECREF(continuityValue);
    Py_DECREF(externalCountValue);
    Py_DECREF(receiptValue);
    return result;
}

PyObject* externalGeometryDiagnosticResult(
                                           std::unique_ptr<ExternalGeometryDiagnostic> diagnostic,
                                           const char* sourceObjectName,
                                           const char* sourceSubelement,
                                           bool defining,
                                           bool intersection)
{
    if (!diagnostic || !sourceObjectName || !sourceSubelement
        || diagnostic->geometry.empty() || diagnostic->reference.empty()) {
        PyErr_SetString(PyExc_RuntimeError,
                        "External-geometry diagnosis produced an invalid state.");
        return nullptr;
    }

    PyObject* result = PyDict_New();
    PyObject* objectName = PyUnicode_FromString(sourceObjectName);
    PyObject* subelement = PyUnicode_FromString(sourceSubelement);
    PyObject* definingValue = PyBool_FromLong(defining);
    PyObject* intersectionValue = PyBool_FromLong(intersection);
    PyObject* reference = PyUnicode_FromString(diagnostic->reference.c_str());
    PyObject* type = PyLong_FromLong(static_cast<long>(diagnostic->type));
    PyObject* referenceIndex = PyLong_FromLong(diagnostic->referenceIndex);
    PyObject* addedReference = PyBool_FromLong(diagnostic->addedReference);
    PyObject* actualDefining = PyBool_FromLong(diagnostic->defining);
    PyObject* geometryCount = PyLong_FromSize_t(diagnostic->geometry.size());
    PyObject* geometry = PyList_New(static_cast<Py_ssize_t>(diagnostic->geometry.size()));
    PyObject* metadata = PyList_New(static_cast<Py_ssize_t>(diagnostic->geometry.size()));
    for (std::size_t index = 0; index < diagnostic->geometry.size(); ++index) {
        PyList_SET_ITEM(geometry,
                        static_cast<Py_ssize_t>(index),
                        diagnostic->geometry[index]->getPyObject());
        const auto facade = ExternalGeometryFacade::getFacade(
            diagnostic->geometry[index].get());
        PyObject* item = PyDict_New();
        PyObject* itemReference = PyUnicode_FromString(facade->getRef().c_str());
        PyObject* itemDefining = PyBool_FromLong(
            facade->testFlag(ExternalGeometryExtension::Defining));
        PyObject* itemFrozen = PyBool_FromLong(
            facade->testFlag(ExternalGeometryExtension::Frozen));
        PyObject* itemDetached = PyBool_FromLong(
            facade->testFlag(ExternalGeometryExtension::Detached));
        PyObject* itemMissing = PyBool_FromLong(
            facade->testFlag(ExternalGeometryExtension::Missing));
        PyObject* itemSynchronized = PyBool_FromLong(
            facade->testFlag(ExternalGeometryExtension::Sync));
        PyDict_SetItemString(item, "reference", itemReference);
        PyDict_SetItemString(item, "defining", itemDefining);
        PyDict_SetItemString(item, "frozen", itemFrozen);
        PyDict_SetItemString(item, "detached", itemDetached);
        PyDict_SetItemString(item, "missing", itemMissing);
        PyDict_SetItemString(item, "synchronized", itemSynchronized);
        Py_DECREF(itemReference);
        Py_DECREF(itemDefining);
        Py_DECREF(itemFrozen);
        Py_DECREF(itemDetached);
        Py_DECREF(itemMissing);
        Py_DECREF(itemSynchronized);
        PyList_SET_ITEM(metadata, static_cast<Py_ssize_t>(index), item);
    }

    PyDict_SetItemString(result, "source_object_name", objectName);
    PyDict_SetItemString(result, "source_subelement", subelement);
    PyDict_SetItemString(result, "requested_defining", definingValue);
    PyDict_SetItemString(result, "requested_intersection", intersectionValue);
    PyDict_SetItemString(result, "reference", reference);
    PyDict_SetItemString(result, "type", type);
    PyDict_SetItemString(result, "reference_index", referenceIndex);
    PyDict_SetItemString(result, "added_reference", addedReference);
    PyDict_SetItemString(result, "defining", actualDefining);
    PyDict_SetItemString(result, "external_geometry_count", geometryCount);
    PyDict_SetItemString(result, "external_geometry", geometry);
    PyDict_SetItemString(result, "external_geometry_metadata", metadata);
    Py_DECREF(objectName);
    Py_DECREF(subelement);
    Py_DECREF(definingValue);
    Py_DECREF(intersectionValue);
    Py_DECREF(reference);
    Py_DECREF(type);
    Py_DECREF(referenceIndex);
    Py_DECREF(addedReference);
    Py_DECREF(actualDefining);
    Py_DECREF(geometryCount);
    Py_DECREF(geometry);
    Py_DECREF(metadata);
    return result;
}

PyObject* carbonCopyDiagnosticResult(std::unique_ptr<CarbonCopyDiagnostic> diagnostic,
                                     const SketchMutationSnapshot& before,
                                     const char* sourceObjectName,
                                     bool construction,
                                     bool allowOtherBody,
                                     bool allowUnaligned)
{
    if (!diagnostic || !diagnostic->sketch || !sourceObjectName) {
        PyErr_SetString(PyExc_RuntimeError, "Carbon Copy diagnosis produced an invalid state.");
        return nullptr;
    }
    auto* sketch = diagnostic->sketch.get();
    const auto& objects = sketch->ExternalGeometry.getValues();
    const auto& subelements = sketch->ExternalGeometry.getSubValues();
    auto types = sketch->ExternalTypes.getValues();
    if (objects.size() != subelements.size()) {
        PyErr_SetString(PyExc_RuntimeError,
                        "Carbon Copy diagnosis produced inconsistent external references.");
        return nullptr;
    }
    types.resize(objects.size(), static_cast<long>(ExtType::Projection));

    PyObject* result = solverDiagnosticResult(sketch, sketch->getLastDoF());
    addDiagnosticSketchState(result, sketch);
    PyObject* sourceName = PyUnicode_FromString(sourceObjectName);
    PyObject* constructionValue = PyBool_FromLong(construction);
    PyObject* allowOtherBodyValue = PyBool_FromLong(allowOtherBody);
    PyObject* allowUnalignedValue = PyBool_FromLong(allowUnaligned);
    PyObject* xInvertedValue = PyBool_FromLong(diagnostic->xInverted);
    PyObject* yInvertedValue = PyBool_FromLong(diagnostic->yInverted);
    PyObject* copiedGeometryCount = PyLong_FromLong(diagnostic->copiedGeometryCount);
    PyObject* copiedConstraintCount = PyLong_FromLong(diagnostic->copiedConstraintCount);
    PyObject* copiedExternalCount = PyLong_FromLong(diagnostic->copiedExternalReferenceCount);
    PyObject* externalReferences = PyList_New(static_cast<Py_ssize_t>(objects.size()));
    for (std::size_t index = 0; index < objects.size(); ++index) {
        PyObject* item = PyDict_New();
        const char* name = objects[index] && objects[index]->getNameInDocument()
            ? objects[index]->getNameInDocument()
            : "";
        PyObject* objectName = PyUnicode_FromString(name);
        PyObject* subelement = PyUnicode_FromString(subelements[index].c_str());
        PyObject* type = PyLong_FromLong(types[index]);
        PyDict_SetItemString(item, "object_name", objectName);
        PyDict_SetItemString(item, "subelement", subelement);
        PyDict_SetItemString(item, "type", type);
        Py_DECREF(objectName);
        Py_DECREF(subelement);
        Py_DECREF(type);
        PyList_SET_ITEM(externalReferences, static_cast<Py_ssize_t>(index), item);
    }

    const auto& rawExternalGeometry = sketch->ExternalGeo.getValues();
    const std::size_t externalGeometryCount = rawExternalGeometry.size() > 2
        ? rawExternalGeometry.size() - 2
        : 0;
    PyObject* externalGeometry = PyList_New(static_cast<Py_ssize_t>(externalGeometryCount));
    PyObject* externalMetadata = PyList_New(static_cast<Py_ssize_t>(externalGeometryCount));
    for (std::size_t index = 0; index < externalGeometryCount; ++index) {
        auto* geometry = rawExternalGeometry[index + 2];
        auto facade = ExternalGeometryFacade::getFacade(geometry);
        PyList_SET_ITEM(externalGeometry, static_cast<Py_ssize_t>(index), geometry->getPyObject());
        PyObject* metadata = PyDict_New();
        PyObject* reference = PyUnicode_FromString(facade->getRef().c_str());
        PyObject* defining = PyBool_FromLong(
            facade->testFlag(ExternalGeometryExtension::Defining));
        PyObject* frozen = PyBool_FromLong(
            facade->testFlag(ExternalGeometryExtension::Frozen));
        PyObject* detached = PyBool_FromLong(
            facade->testFlag(ExternalGeometryExtension::Detached));
        PyObject* missing = PyBool_FromLong(
            facade->testFlag(ExternalGeometryExtension::Missing));
        PyObject* synchronized = PyBool_FromLong(
            facade->testFlag(ExternalGeometryExtension::Sync));
        PyDict_SetItemString(metadata, "reference", reference);
        PyDict_SetItemString(metadata, "defining", defining);
        PyDict_SetItemString(metadata, "frozen", frozen);
        PyDict_SetItemString(metadata, "detached", detached);
        PyDict_SetItemString(metadata, "missing", missing);
        PyDict_SetItemString(metadata, "synchronized", synchronized);
        Py_DECREF(reference);
        Py_DECREF(defining);
        Py_DECREF(frozen);
        Py_DECREF(detached);
        Py_DECREF(missing);
        Py_DECREF(synchronized);
        PyList_SET_ITEM(externalMetadata, static_cast<Py_ssize_t>(index), metadata);
    }

    PyObject* expressionValues = PyList_New(
        static_cast<Py_ssize_t>(diagnostic->expressions.size()));
    for (std::size_t index = 0; index < diagnostic->expressions.size(); ++index) {
        PyObject* item = PyDict_New();
        PyObject* constraintIndex = PyLong_FromLong(
            diagnostic->expressions[index].constraintIndex);
        PyObject* path = PyUnicode_FromString(diagnostic->expressions[index].path.c_str());
        PyObject* expression = PyUnicode_FromString(
            diagnostic->expressions[index].expression.c_str());
        PyDict_SetItemString(item, "constraint_index", constraintIndex);
        PyDict_SetItemString(item, "path", path);
        PyDict_SetItemString(item, "expression", expression);
        Py_DECREF(constraintIndex);
        Py_DECREF(path);
        Py_DECREF(expression);
        PyList_SET_ITEM(expressionValues, static_cast<Py_ssize_t>(index), item);
    }
    PyObject* externalReferenceCount = PyLong_FromSize_t(objects.size());
    PyObject* externalGeometryCountValue = PyLong_FromSize_t(externalGeometryCount);
    PyObject* receipt = mutationResult(before, sketch);

    PyDict_SetItemString(result, "source_object_name", sourceName);
    PyDict_SetItemString(result, "requested_construction", constructionValue);
    PyDict_SetItemString(result, "requested_allow_other_body", allowOtherBodyValue);
    PyDict_SetItemString(result, "requested_allow_unaligned", allowUnalignedValue);
    PyDict_SetItemString(result, "x_inverted", xInvertedValue);
    PyDict_SetItemString(result, "y_inverted", yInvertedValue);
    PyDict_SetItemString(result, "copied_geometry_count", copiedGeometryCount);
    PyDict_SetItemString(result, "copied_constraint_count", copiedConstraintCount);
    PyDict_SetItemString(result, "copied_external_reference_count", copiedExternalCount);
    PyDict_SetItemString(result, "external_reference_count", externalReferenceCount);
    PyDict_SetItemString(result, "external_references", externalReferences);
    PyDict_SetItemString(result, "external_geometry_count", externalGeometryCountValue);
    PyDict_SetItemString(result, "external_geometry", externalGeometry);
    PyDict_SetItemString(result, "external_geometry_metadata", externalMetadata);
    PyDict_SetItemString(result, "expressions", expressionValues);
    PyDict_SetItemString(result, "mutation_receipt", receipt);
    Py_DECREF(sourceName);
    Py_DECREF(constructionValue);
    Py_DECREF(allowOtherBodyValue);
    Py_DECREF(allowUnalignedValue);
    Py_DECREF(xInvertedValue);
    Py_DECREF(yInvertedValue);
    Py_DECREF(copiedGeometryCount);
    Py_DECREF(copiedConstraintCount);
    Py_DECREF(copiedExternalCount);
    Py_DECREF(externalReferenceCount);
    Py_DECREF(externalReferences);
    Py_DECREF(externalGeometryCountValue);
    Py_DECREF(externalGeometry);
    Py_DECREF(externalMetadata);
    Py_DECREF(expressionValues);
    Py_DECREF(receipt);
    return result;
}

bool geometryIdsFromPython(
    PyObject* object,
    std::vector<int>& geometryIds,
    const char* operation
)
{
    const std::string typeMessage = std::string(operation) + " geometry IDs must be a sequence.";
    if (!PyList_Check(object) && !PyTuple_Check(object)) {
        PyErr_SetString(PyExc_TypeError, typeMessage.c_str());
        return false;
    }
    PyObject* sequence = PySequence_Fast(object, typeMessage.c_str());
    if (!sequence) {
        return false;
    }
    const Py_ssize_t count = PySequence_Fast_GET_SIZE(sequence);
    if (count < 1 || count > 1'000'000) {
        Py_DECREF(sequence);
        const std::string countMessage = std::string(operation)
            + " requires one through one million geometry IDs.";
        PyErr_SetString(PyExc_ValueError, countMessage.c_str());
        return false;
    }
    geometryIds.reserve(static_cast<std::size_t>(count));
    for (Py_ssize_t index = 0; index < count; ++index) {
        PyObject* item = PySequence_Fast_GET_ITEM(sequence, index);
        if (!PyLong_Check(item) || PyBool_Check(item)) {
            Py_DECREF(sequence);
            const std::string itemMessage = std::string("Every ") + operation
                + " geometry ID must be an integer.";
            PyErr_SetString(PyExc_TypeError, itemMessage.c_str());
            return false;
        }
        const long value = PyLong_AsLong(item);
        if (PyErr_Occurred()) {
            Py_DECREF(sequence);
            return false;
        }
        geometryIds.push_back(static_cast<int>(value));
    }
    Py_DECREF(sequence);
    return true;
}

PyObject* vector2dResult(const Base::Vector3d& vector)
{
    PyObject* result = PyDict_New();
    PyObject* x = PyFloat_FromDouble(vector.x);
    PyObject* y = PyFloat_FromDouble(vector.y);
    PyDict_SetItemString(result, "x", x);
    PyDict_SetItemString(result, "y", y);
    Py_DECREF(x);
    Py_DECREF(y);
    return result;
}

void addTranslateExternalState(PyObject* result, SketchObject* sketch)
{
    const auto& objects = sketch->ExternalGeometry.getValues();
    const auto& subelements = sketch->ExternalGeometry.getSubValues();
    auto types = sketch->ExternalTypes.getValues();
    types.resize(objects.size(), static_cast<long>(ExtType::Projection));

    PyObject* references = PyList_New(static_cast<Py_ssize_t>(objects.size()));
    for (std::size_t index = 0; index < objects.size(); ++index) {
        PyObject* item = PyDict_New();
        const char* name = objects[index] && objects[index]->getNameInDocument()
            ? objects[index]->getNameInDocument()
            : "";
        PyObject* objectName = PyUnicode_FromString(name);
        PyObject* subelement = PyUnicode_FromString(
            index < subelements.size() ? subelements[index].c_str() : "");
        PyObject* type = PyLong_FromLong(types[index]);
        PyDict_SetItemString(item, "object_name", objectName);
        PyDict_SetItemString(item, "subelement", subelement);
        PyDict_SetItemString(item, "type", type);
        Py_DECREF(objectName);
        Py_DECREF(subelement);
        Py_DECREF(type);
        PyList_SET_ITEM(references, static_cast<Py_ssize_t>(index), item);
    }

    const auto& rawExternalGeometry = sketch->ExternalGeo.getValues();
    const std::size_t geometryCount = rawExternalGeometry.size() > 2
        ? rawExternalGeometry.size() - 2
        : 0;
    PyObject* geometry = PyList_New(static_cast<Py_ssize_t>(geometryCount));
    PyObject* metadata = PyList_New(static_cast<Py_ssize_t>(geometryCount));
    for (std::size_t index = 0; index < geometryCount; ++index) {
        auto* value = rawExternalGeometry[index + 2];
        auto facade = ExternalGeometryFacade::getFacade(value);
        PyList_SET_ITEM(geometry, static_cast<Py_ssize_t>(index), value->getPyObject());
        PyObject* item = PyDict_New();
        PyObject* reference = PyUnicode_FromString(facade->getRef().c_str());
        PyObject* defining = PyBool_FromLong(
            facade->testFlag(ExternalGeometryExtension::Defining));
        PyObject* frozen = PyBool_FromLong(
            facade->testFlag(ExternalGeometryExtension::Frozen));
        PyObject* detached = PyBool_FromLong(
            facade->testFlag(ExternalGeometryExtension::Detached));
        PyObject* missing = PyBool_FromLong(
            facade->testFlag(ExternalGeometryExtension::Missing));
        PyObject* synchronized = PyBool_FromLong(
            facade->testFlag(ExternalGeometryExtension::Sync));
        PyDict_SetItemString(item, "reference", reference);
        PyDict_SetItemString(item, "defining", defining);
        PyDict_SetItemString(item, "frozen", frozen);
        PyDict_SetItemString(item, "detached", detached);
        PyDict_SetItemString(item, "missing", missing);
        PyDict_SetItemString(item, "synchronized", synchronized);
        Py_DECREF(reference);
        Py_DECREF(defining);
        Py_DECREF(frozen);
        Py_DECREF(detached);
        Py_DECREF(missing);
        Py_DECREF(synchronized);
        PyList_SET_ITEM(metadata, static_cast<Py_ssize_t>(index), item);
    }

    PyObject* referenceCount = PyLong_FromSize_t(objects.size());
    PyObject* geometryCountValue = PyLong_FromSize_t(geometryCount);
    PyDict_SetItemString(result, "external_reference_count", referenceCount);
    PyDict_SetItemString(result, "external_references", references);
    PyDict_SetItemString(result, "external_geometry_count", geometryCountValue);
    PyDict_SetItemString(result, "external_geometry", geometry);
    PyDict_SetItemString(result, "external_geometry_metadata", metadata);
    Py_DECREF(referenceCount);
    Py_DECREF(references);
    Py_DECREF(geometryCountValue);
    Py_DECREF(geometry);
    Py_DECREF(metadata);
}

PyObject* translateDiagnosticResult(std::unique_ptr<TranslateDiagnostic> diagnostic,
                                    const SketchMutationSnapshot& before)
{
    if (!diagnostic || !diagnostic->sketch) {
        PyErr_SetString(PyExc_RuntimeError, "Translate diagnosis produced an invalid state.");
        return nullptr;
    }
    auto* sketch = diagnostic->sketch.get();
    const auto after = mutationSnapshot(sketch);
    PyObject* result = solverDiagnosticResult(sketch, sketch->getLastDoF());
    addDiagnosticSketchState(result, sketch);
    addTranslateExternalState(result, sketch);

    PyObject* geometryIds = PyList_New(static_cast<Py_ssize_t>(diagnostic->geometryIds.size()));
    for (std::size_t index = 0; index < diagnostic->geometryIds.size(); ++index) {
        PyList_SET_ITEM(geometryIds,
                        static_cast<Py_ssize_t>(index),
                        PyLong_FromLong(diagnostic->geometryIds[index]));
    }
    PyObject* firstVector = vector2dResult(diagnostic->firstVector);
    PyObject* secondVector = vector2dResult(diagnostic->secondVector);
    PyObject* copyCount = PyLong_FromLong(diagnostic->copyCount);
    PyObject* rowCount = PyLong_FromLong(diagnostic->rowCount);
    PyObject* equalize = PyBool_FromLong(diagnostic->equalizeDimensionalConstraints);
    PyObject* deletedOriginals = PyBool_FromLong(diagnostic->deletedOriginals);
    PyObject* expressionValues = PyList_New(
        static_cast<Py_ssize_t>(diagnostic->expressions.size()));
    for (std::size_t index = 0; index < diagnostic->expressions.size(); ++index) {
        const auto& expression = diagnostic->expressions[index];
        PyObject* item = PyDict_New();
        PyObject* constraintIndex = PyLong_FromLong(expression.constraintIndex);
        PyObject* path = PyUnicode_FromString(expression.path.c_str());
        PyObject* value = PyUnicode_FromString(expression.expression.c_str());
        PyDict_SetItemString(item, "constraint_index", constraintIndex);
        PyDict_SetItemString(item, "path", path);
        PyDict_SetItemString(item, "expression", value);
        Py_DECREF(constraintIndex);
        Py_DECREF(path);
        Py_DECREF(value);
        PyList_SET_ITEM(expressionValues, static_cast<Py_ssize_t>(index), item);
    }
    auto tagValues = [](const std::vector<std::string>& tags) {
        PyObject* values = PyList_New(static_cast<Py_ssize_t>(tags.size()));
        for (std::size_t index = 0; index < tags.size(); ++index) {
            PyList_SET_ITEM(values,
                            static_cast<Py_ssize_t>(index),
                            PyUnicode_FromString(tags[index].c_str()));
        }
        return values;
    };
    PyObject* geometryTags = tagValues(after.geometryTags);
    PyObject* constraintTags = tagValues(after.constraintTags);
    PyObject* receipt = mutationResult(before, sketch);
    PyDict_SetItemString(result, "input_geometry_indices", geometryIds);
    PyDict_SetItemString(result, "first_vector_mm", firstVector);
    PyDict_SetItemString(result, "copy_count", copyCount);
    PyDict_SetItemString(result, "second_vector_mm", secondVector);
    PyDict_SetItemString(result, "row_count", rowCount);
    PyDict_SetItemString(result, "equalize_dimensional_constraints", equalize);
    PyDict_SetItemString(result, "deleted_originals", deletedOriginals);
    PyDict_SetItemString(result, "geometry_tags", geometryTags);
    PyDict_SetItemString(result, "constraint_tags", constraintTags);
    PyDict_SetItemString(result, "expressions", expressionValues);
    PyDict_SetItemString(result, "mutation_receipt", receipt);
    Py_DECREF(geometryIds);
    Py_DECREF(firstVector);
    Py_DECREF(copyCount);
    Py_DECREF(secondVector);
    Py_DECREF(rowCount);
    Py_DECREF(equalize);
    Py_DECREF(deletedOriginals);
    Py_DECREF(geometryTags);
    Py_DECREF(constraintTags);
    Py_DECREF(expressionValues);
    Py_DECREF(receipt);
    return result;
}

PyObject* rotateDiagnosticResult(std::unique_ptr<RotateDiagnostic> diagnostic,
                                 const SketchMutationSnapshot& before)
{
    if (!diagnostic || !diagnostic->sketch) {
        PyErr_SetString(PyExc_RuntimeError, "Rotate diagnosis produced an invalid state.");
        return nullptr;
    }
    auto* sketch = diagnostic->sketch.get();
    const auto after = mutationSnapshot(sketch);
    PyObject* result = solverDiagnosticResult(sketch, sketch->getLastDoF());
    addDiagnosticSketchState(result, sketch);
    addTranslateExternalState(result, sketch);

    PyObject* geometryIds = PyList_New(static_cast<Py_ssize_t>(diagnostic->geometryIds.size()));
    for (std::size_t index = 0; index < diagnostic->geometryIds.size(); ++index) {
        PyList_SET_ITEM(geometryIds,
                        static_cast<Py_ssize_t>(index),
                        PyLong_FromLong(diagnostic->geometryIds[index]));
    }
    PyObject* center = vector2dResult(diagnostic->center);
    PyObject* totalAngle = PyFloat_FromDouble(diagnostic->totalAngleRadians);
    PyObject* copyCount = PyLong_FromLong(diagnostic->copyCount);
    PyObject* equalize = PyBool_FromLong(diagnostic->equalizeDimensionalConstraints);
    PyObject* deletedOriginals = PyBool_FromLong(diagnostic->deletedOriginals);
    PyObject* expressionValues = PyList_New(
        static_cast<Py_ssize_t>(diagnostic->expressions.size()));
    for (std::size_t index = 0; index < diagnostic->expressions.size(); ++index) {
        const auto& expression = diagnostic->expressions[index];
        PyObject* item = PyDict_New();
        PyObject* constraintIndex = PyLong_FromLong(expression.constraintIndex);
        PyObject* path = PyUnicode_FromString(expression.path.c_str());
        PyObject* value = PyUnicode_FromString(expression.expression.c_str());
        PyDict_SetItemString(item, "constraint_index", constraintIndex);
        PyDict_SetItemString(item, "path", path);
        PyDict_SetItemString(item, "expression", value);
        Py_DECREF(constraintIndex);
        Py_DECREF(path);
        Py_DECREF(value);
        PyList_SET_ITEM(expressionValues, static_cast<Py_ssize_t>(index), item);
    }
    auto tagValues = [](const std::vector<std::string>& tags) {
        PyObject* values = PyList_New(static_cast<Py_ssize_t>(tags.size()));
        for (std::size_t index = 0; index < tags.size(); ++index) {
            PyList_SET_ITEM(values,
                            static_cast<Py_ssize_t>(index),
                            PyUnicode_FromString(tags[index].c_str()));
        }
        return values;
    };
    PyObject* geometryTags = tagValues(after.geometryTags);
    PyObject* constraintTags = tagValues(after.constraintTags);
    PyObject* receipt = mutationResult(before, sketch);
    PyDict_SetItemString(result, "input_geometry_indices", geometryIds);
    PyDict_SetItemString(result, "center_mm", center);
    PyDict_SetItemString(result, "total_angle_radians", totalAngle);
    PyDict_SetItemString(result, "copy_count", copyCount);
    PyDict_SetItemString(result, "equalize_dimensional_constraints", equalize);
    PyDict_SetItemString(result, "deleted_originals", deletedOriginals);
    PyDict_SetItemString(result, "geometry_tags", geometryTags);
    PyDict_SetItemString(result, "constraint_tags", constraintTags);
    PyDict_SetItemString(result, "expressions", expressionValues);
    PyDict_SetItemString(result, "mutation_receipt", receipt);
    Py_DECREF(geometryIds);
    Py_DECREF(center);
    Py_DECREF(totalAngle);
    Py_DECREF(copyCount);
    Py_DECREF(equalize);
    Py_DECREF(deletedOriginals);
    Py_DECREF(geometryTags);
    Py_DECREF(constraintTags);
    Py_DECREF(expressionValues);
    Py_DECREF(receipt);
    return result;
}

PyObject* scaleDiagnosticResult(std::unique_ptr<ScaleDiagnostic> diagnostic,
                                const SketchMutationSnapshot& before)
{
    if (!diagnostic || !diagnostic->sketch) {
        PyErr_SetString(PyExc_RuntimeError, "Scale diagnosis produced an invalid state.");
        return nullptr;
    }
    auto* sketch = diagnostic->sketch.get();
    const auto after = mutationSnapshot(sketch);
    PyObject* result = solverDiagnosticResult(sketch, sketch->getLastDoF());
    addDiagnosticSketchState(result, sketch);
    addTranslateExternalState(result, sketch);

    PyObject* geometryIds = PyList_New(static_cast<Py_ssize_t>(diagnostic->geometryIds.size()));
    for (std::size_t index = 0; index < diagnostic->geometryIds.size(); ++index) {
        PyList_SET_ITEM(geometryIds,
                        static_cast<Py_ssize_t>(index),
                        PyLong_FromLong(diagnostic->geometryIds[index]));
    }
    auto tagValues = [](const std::vector<std::string>& tags) {
        PyObject* values = PyList_New(static_cast<Py_ssize_t>(tags.size()));
        for (std::size_t index = 0; index < tags.size(); ++index) {
            PyList_SET_ITEM(values,
                            static_cast<Py_ssize_t>(index),
                            PyUnicode_FromString(tags[index].c_str()));
        }
        return values;
    };
    PyObject* center = vector2dResult(diagnostic->center);
    PyObject* scaleFactor = PyFloat_FromDouble(diagnostic->scaleFactor);
    PyObject* keepOriginals = PyBool_FromLong(diagnostic->keepOriginals);
    PyObject* allowOriginConstraints = PyBool_FromLong(diagnostic->allowOriginConstraints);
    PyObject* deletedOriginals = PyBool_FromLong(diagnostic->deletedOriginals);
    PyObject* geometryTags = tagValues(after.geometryTags);
    PyObject* constraintTags = tagValues(after.constraintTags);
    PyObject* expressionValues = PyList_New(0);
    PyObject* receipt = mutationResult(before, sketch);
    PyDict_SetItemString(result, "input_geometry_indices", geometryIds);
    PyDict_SetItemString(result, "center_mm", center);
    PyDict_SetItemString(result, "scale_factor", scaleFactor);
    PyDict_SetItemString(result, "keep_originals", keepOriginals);
    PyDict_SetItemString(result, "allow_origin_constraints", allowOriginConstraints);
    PyDict_SetItemString(result, "deleted_originals", deletedOriginals);
    PyDict_SetItemString(result, "geometry_tags", geometryTags);
    PyDict_SetItemString(result, "constraint_tags", constraintTags);
    PyDict_SetItemString(result, "expressions", expressionValues);
    PyDict_SetItemString(result, "mutation_receipt", receipt);
    Py_DECREF(geometryIds);
    Py_DECREF(center);
    Py_DECREF(scaleFactor);
    Py_DECREF(keepOriginals);
    Py_DECREF(allowOriginConstraints);
    Py_DECREF(deletedOriginals);
    Py_DECREF(geometryTags);
    Py_DECREF(constraintTags);
    Py_DECREF(expressionValues);
    Py_DECREF(receipt);
    return result;
}

PyObject* offsetDiagnosticResult(std::unique_ptr<OffsetDiagnostic> diagnostic,
                                 const SketchMutationSnapshot& before)
{
    if (!diagnostic || !diagnostic->sketch) {
        PyErr_SetString(PyExc_RuntimeError, "Offset diagnosis produced an invalid state.");
        return nullptr;
    }
    auto* sketch = diagnostic->sketch.get();
    const auto after = mutationSnapshot(sketch);
    PyObject* result = solverDiagnosticResult(sketch, sketch->getLastDoF());
    addDiagnosticSketchState(result, sketch);
    addTranslateExternalState(result, sketch);

    PyObject* geometryIds = PyList_New(static_cast<Py_ssize_t>(diagnostic->geometryIds.size()));
    for (std::size_t index = 0; index < diagnostic->geometryIds.size(); ++index) {
        PyList_SET_ITEM(geometryIds,
                        static_cast<Py_ssize_t>(index),
                        PyLong_FromLong(diagnostic->geometryIds[index]));
    }
    auto tagValues = [](const std::vector<std::string>& tags) {
        PyObject* values = PyList_New(static_cast<Py_ssize_t>(tags.size()));
        for (std::size_t index = 0; index < tags.size(); ++index) {
            PyList_SET_ITEM(values,
                            static_cast<Py_ssize_t>(index),
                            PyUnicode_FromString(tags[index].c_str()));
        }
        return values;
    };
    const char* joinType = diagnostic->joinType == OffsetJoinType::Arc ? "arc"
                                                                       : "intersection";
    const char* sourceMode = diagnostic->sourceMode == OffsetSourceMode::Keep
        ? "keep"
        : diagnostic->sourceMode == OffsetSourceMode::Delete ? "delete" : "constrain";
    PyObject* offsetLength = PyFloat_FromDouble(diagnostic->offsetLength);
    PyObject* joinTypeValue = PyUnicode_FromString(joinType);
    PyObject* sourceModeValue = PyUnicode_FromString(sourceMode);
    PyObject* deletedOriginals = PyBool_FromLong(diagnostic->deletedOriginals);
    PyObject* constrainedOffset = PyBool_FromLong(diagnostic->constrainedOffset);
    PyObject* geometryTags = tagValues(after.geometryTags);
    PyObject* constraintTags = tagValues(after.constraintTags);
    PyObject* expressionValues = PyList_New(0);
    PyObject* receipt = mutationResult(before, sketch);
    PyDict_SetItemString(result, "input_geometry_indices", geometryIds);
    PyDict_SetItemString(result, "offset_length_mm", offsetLength);
    PyDict_SetItemString(result, "join_type", joinTypeValue);
    PyDict_SetItemString(result, "source_mode", sourceModeValue);
    PyDict_SetItemString(result, "deleted_originals", deletedOriginals);
    PyDict_SetItemString(result, "constrained_offset", constrainedOffset);
    PyDict_SetItemString(result, "geometry_tags", geometryTags);
    PyDict_SetItemString(result, "constraint_tags", constraintTags);
    PyDict_SetItemString(result, "expressions", expressionValues);
    PyDict_SetItemString(result, "mutation_receipt", receipt);
    Py_DECREF(geometryIds);
    Py_DECREF(offsetLength);
    Py_DECREF(joinTypeValue);
    Py_DECREF(sourceModeValue);
    Py_DECREF(deletedOriginals);
    Py_DECREF(constrainedOffset);
    Py_DECREF(geometryTags);
    Py_DECREF(constraintTags);
    Py_DECREF(expressionValues);
    Py_DECREF(receipt);
    return result;
}

PyObject* symmetryDiagnosticResult(std::unique_ptr<SymmetryDiagnostic> diagnostic,
                                   const SketchMutationSnapshot& before)
{
    if (!diagnostic || !diagnostic->sketch) {
        PyErr_SetString(PyExc_RuntimeError, "Symmetry diagnosis produced an invalid state.");
        return nullptr;
    }
    auto* sketch = diagnostic->sketch.get();
    const auto after = mutationSnapshot(sketch);
    PyObject* result = solverDiagnosticResult(sketch, sketch->getLastDoF());
    addDiagnosticSketchState(result, sketch);
    addTranslateExternalState(result, sketch);

    PyObject* geometryIds = PyList_New(static_cast<Py_ssize_t>(diagnostic->geometryIds.size()));
    for (std::size_t index = 0; index < diagnostic->geometryIds.size(); ++index) {
        PyList_SET_ITEM(geometryIds,
                        static_cast<Py_ssize_t>(index),
                        PyLong_FromLong(diagnostic->geometryIds[index]));
    }
    auto tagValues = [](const std::vector<std::string>& tags) {
        PyObject* values = PyList_New(static_cast<Py_ssize_t>(tags.size()));
        for (std::size_t index = 0; index < tags.size(); ++index) {
            PyList_SET_ITEM(values,
                            static_cast<Py_ssize_t>(index),
                            PyUnicode_FromString(tags[index].c_str()));
        }
        return values;
    };
    const char* position = diagnostic->referencePosition == PointPos::none
        ? "whole"
        : diagnostic->referencePosition == PointPos::start
        ? "start"
        : diagnostic->referencePosition == PointPos::end ? "end" : "center";
    const char* sourceMode = diagnostic->sourceMode == SymmetrySourceMode::Keep
        ? "keep"
        : diagnostic->sourceMode == SymmetrySourceMode::Delete ? "delete" : "constrain";
    PyObject* referenceGeometry = PyLong_FromLong(diagnostic->referenceGeometryId);
    PyObject* referencePosition = PyUnicode_FromString(position);
    PyObject* sourceModeValue = PyUnicode_FromString(sourceMode);
    PyObject* deletedOriginals = PyBool_FromLong(diagnostic->deletedOriginals);
    PyObject* constrainedSymmetry = PyBool_FromLong(diagnostic->constrainedSymmetry);
    PyObject* geometryTags = tagValues(after.geometryTags);
    PyObject* constraintTags = tagValues(after.constraintTags);
    PyObject* expressionValues = PyList_New(0);
    PyObject* receipt = mutationResult(before, sketch);
    PyDict_SetItemString(result, "input_geometry_indices", geometryIds);
    PyDict_SetItemString(result, "reference_geometry_index", referenceGeometry);
    PyDict_SetItemString(result, "reference_position", referencePosition);
    PyDict_SetItemString(result, "source_mode", sourceModeValue);
    PyDict_SetItemString(result, "deleted_originals", deletedOriginals);
    PyDict_SetItemString(result, "constrained_symmetry", constrainedSymmetry);
    PyDict_SetItemString(result, "geometry_tags", geometryTags);
    PyDict_SetItemString(result, "constraint_tags", constraintTags);
    PyDict_SetItemString(result, "expressions", expressionValues);
    PyDict_SetItemString(result, "mutation_receipt", receipt);
    Py_DECREF(geometryIds);
    Py_DECREF(referenceGeometry);
    Py_DECREF(referencePosition);
    Py_DECREF(sourceModeValue);
    Py_DECREF(deletedOriginals);
    Py_DECREF(constrainedSymmetry);
    Py_DECREF(geometryTags);
    Py_DECREF(constraintTags);
    Py_DECREF(expressionValues);
    Py_DECREF(receipt);
    return result;
}

PyObject* axisAlignmentRemovalDiagnosticResult(
    std::unique_ptr<AxisAlignmentRemovalDiagnostic> diagnostic,
    const SketchMutationSnapshot& before
)
{
    if (!diagnostic || !diagnostic->sketch) {
        PyErr_SetString(PyExc_RuntimeError,
                        "Remove Axes Alignment diagnosis produced an invalid state.");
        return nullptr;
    }
    auto* sketch = diagnostic->sketch.get();
    const auto after = mutationSnapshot(sketch);
    PyObject* result = solverDiagnosticResult(sketch, sketch->getLastDoF());
    addDiagnosticSketchState(result, sketch);
    addTranslateExternalState(result, sketch);

    PyObject* geometryIds = PyList_New(static_cast<Py_ssize_t>(diagnostic->geometryIds.size()));
    for (std::size_t index = 0; index < diagnostic->geometryIds.size(); ++index) {
        PyList_SET_ITEM(geometryIds,
                        static_cast<Py_ssize_t>(index),
                        PyLong_FromLong(diagnostic->geometryIds[index]));
    }
    auto tagValues = [](const std::vector<std::string>& tags) {
        PyObject* values = PyList_New(static_cast<Py_ssize_t>(tags.size()));
        for (std::size_t index = 0; index < tags.size(); ++index) {
            PyList_SET_ITEM(values,
                            static_cast<Py_ssize_t>(index),
                            PyUnicode_FromString(tags[index].c_str()));
        }
        return values;
    };
    PyObject* removedHorizontal = PyLong_FromLong(diagnostic->removedHorizontalConstraints);
    PyObject* removedVertical = PyLong_FromLong(diagnostic->removedVerticalConstraints);
    PyObject* createdParallel = PyLong_FromLong(diagnostic->createdParallelConstraints);
    PyObject* removedSymmetry = PyLong_FromLong(diagnostic->removedAxisSymmetryConstraints);
    PyObject* removedPointOnAxis = PyLong_FromLong(diagnostic->removedPointOnAxisConstraints);
    PyObject* convertedDistance = PyLong_FromLong(diagnostic->convertedDistanceConstraints);
    PyObject* geometryTags = tagValues(after.geometryTags);
    PyObject* constraintTags = tagValues(after.constraintTags);
    PyObject* expressionValues = PyList_New(0);
    PyObject* receipt = mutationResult(before, sketch);
    PyDict_SetItemString(result, "input_geometry_indices", geometryIds);
    PyDict_SetItemString(result, "removed_horizontal_constraints", removedHorizontal);
    PyDict_SetItemString(result, "removed_vertical_constraints", removedVertical);
    PyDict_SetItemString(result, "created_parallel_constraints", createdParallel);
    PyDict_SetItemString(result, "removed_axis_symmetry_constraints", removedSymmetry);
    PyDict_SetItemString(result, "removed_point_on_axis_constraints", removedPointOnAxis);
    PyDict_SetItemString(result, "converted_distance_constraints", convertedDistance);
    PyDict_SetItemString(result, "geometry_tags", geometryTags);
    PyDict_SetItemString(result, "constraint_tags", constraintTags);
    PyDict_SetItemString(result, "expressions", expressionValues);
    PyDict_SetItemString(result, "mutation_receipt", receipt);
    Py_DECREF(geometryIds);
    Py_DECREF(removedHorizontal);
    Py_DECREF(removedVertical);
    Py_DECREF(createdParallel);
    Py_DECREF(removedSymmetry);
    Py_DECREF(removedPointOnAxis);
    Py_DECREF(convertedDistance);
    Py_DECREF(geometryTags);
    Py_DECREF(constraintTags);
    Py_DECREF(expressionValues);
    Py_DECREF(receipt);
    return result;
}

PyObject* nurbsConversionDiagnosticResult(
    std::unique_ptr<NURBSConversionDiagnostic> diagnostic,
    const SketchMutationSnapshot& before
)
{
    if (!diagnostic || !diagnostic->sketch) {
        PyErr_SetString(PyExc_RuntimeError,
                        "Geometry-to-B-Spline diagnosis produced an invalid state.");
        return nullptr;
    }
    auto* sketch = diagnostic->sketch.get();
    const auto after = mutationSnapshot(sketch);
    PyObject* result = solverDiagnosticResult(sketch, sketch->getLastDoF());
    addDiagnosticSketchState(result, sketch);
    addTranslateExternalState(result, sketch);

    auto integerValues = [](const std::vector<int>& values) {
        PyObject* result = PyList_New(static_cast<Py_ssize_t>(values.size()));
        for (std::size_t index = 0; index < values.size(); ++index) {
            PyList_SET_ITEM(result,
                            static_cast<Py_ssize_t>(index),
                            PyLong_FromLong(values[index]));
        }
        return result;
    };
    auto tagValues = [](const std::vector<std::string>& tags) {
        PyObject* values = PyList_New(static_cast<Py_ssize_t>(tags.size()));
        for (std::size_t index = 0; index < tags.size(); ++index) {
            PyList_SET_ITEM(values,
                            static_cast<Py_ssize_t>(index),
                            PyUnicode_FromString(tags[index].c_str()));
        }
        return values;
    };
    PyObject* inputGeometry = integerValues(diagnostic->geometryIds);
    PyObject* convertedGeometry = integerValues(diagnostic->convertedGeometryIds);
    PyObject* exposed = PyLong_FromLong(diagnostic->exposedInternalGeometryCount);
    PyObject* geometryTags = tagValues(after.geometryTags);
    PyObject* constraintTags = tagValues(after.constraintTags);
    PyObject* expressionValues = PyList_New(0);
    PyObject* receipt = mutationResult(before, sketch);
    PyDict_SetItemString(result, "input_geometry_indices", inputGeometry);
    PyDict_SetItemString(result, "converted_geometry_indices", convertedGeometry);
    PyDict_SetItemString(result, "exposed_internal_geometry_count", exposed);
    PyDict_SetItemString(result, "geometry_tags", geometryTags);
    PyDict_SetItemString(result, "constraint_tags", constraintTags);
    PyDict_SetItemString(result, "expressions", expressionValues);
    PyDict_SetItemString(result, "mutation_receipt", receipt);
    Py_DECREF(inputGeometry);
    Py_DECREF(convertedGeometry);
    Py_DECREF(exposed);
    Py_DECREF(geometryTags);
    Py_DECREF(constraintTags);
    Py_DECREF(expressionValues);
    Py_DECREF(receipt);
    return result;
}

PyObject* bsplineDegreeIncreaseDiagnosticResult(
    std::unique_ptr<BSplineDegreeIncreaseDiagnostic> diagnostic,
    const SketchMutationSnapshot& before
)
{
    if (!diagnostic || !diagnostic->sketch) {
        PyErr_SetString(PyExc_RuntimeError,
                        "Increase B-Spline Degree diagnosis produced an invalid state.");
        return nullptr;
    }
    auto* sketch = diagnostic->sketch.get();
    const auto after = mutationSnapshot(sketch);
    PyObject* result = solverDiagnosticResult(sketch, sketch->getLastDoF());
    addDiagnosticSketchState(result, sketch);
    addTranslateExternalState(result, sketch);

    auto integerValues = [](const std::vector<int>& values) {
        PyObject* result = PyList_New(static_cast<Py_ssize_t>(values.size()));
        for (std::size_t index = 0; index < values.size(); ++index) {
            PyList_SET_ITEM(result,
                            static_cast<Py_ssize_t>(index),
                            PyLong_FromLong(values[index]));
        }
        return result;
    };
    auto tagValues = [](const std::vector<std::string>& tags) {
        PyObject* values = PyList_New(static_cast<Py_ssize_t>(tags.size()));
        for (std::size_t index = 0; index < tags.size(); ++index) {
            PyList_SET_ITEM(values,
                            static_cast<Py_ssize_t>(index),
                            PyUnicode_FromString(tags[index].c_str()));
        }
        return values;
    };
    PyObject* inputGeometry = integerValues(diagnostic->geometryIds);
    PyObject* oldDegrees = integerValues(diagnostic->oldDegrees);
    PyObject* newDegrees = integerValues(diagnostic->newDegrees);
    PyObject* exposed = PyLong_FromLong(diagnostic->exposedInternalGeometryCount);
    PyObject* geometryTags = tagValues(after.geometryTags);
    PyObject* constraintTags = tagValues(after.constraintTags);
    PyObject* expressionValues = PyList_New(0);
    PyObject* receipt = mutationResult(before, sketch);
    PyDict_SetItemString(result, "input_geometry_indices", inputGeometry);
    PyDict_SetItemString(result, "old_degrees", oldDegrees);
    PyDict_SetItemString(result, "new_degrees", newDegrees);
    PyDict_SetItemString(result, "exposed_internal_geometry_count", exposed);
    PyDict_SetItemString(result, "geometry_tags", geometryTags);
    PyDict_SetItemString(result, "constraint_tags", constraintTags);
    PyDict_SetItemString(result, "expressions", expressionValues);
    PyDict_SetItemString(result, "mutation_receipt", receipt);
    Py_DECREF(inputGeometry);
    Py_DECREF(oldDegrees);
    Py_DECREF(newDegrees);
    Py_DECREF(exposed);
    Py_DECREF(geometryTags);
    Py_DECREF(constraintTags);
    Py_DECREF(expressionValues);
    Py_DECREF(receipt);
    return result;
}

PyObject* bsplineDegreeDecreaseDiagnosticResult(
    std::unique_ptr<BSplineDegreeDecreaseDiagnostic> diagnostic,
    const SketchMutationSnapshot& before
)
{
    if (!diagnostic || !diagnostic->sketch) {
        PyErr_SetString(PyExc_RuntimeError,
                        "Decrease B-Spline Degree diagnosis produced an invalid state.");
        return nullptr;
    }
    auto* sketch = diagnostic->sketch.get();
    const auto after = mutationSnapshot(sketch);
    PyObject* result = solverDiagnosticResult(sketch, sketch->getLastDoF());
    addDiagnosticSketchState(result, sketch);
    addTranslateExternalState(result, sketch);

    auto tagValues = [](const std::vector<std::string>& tags) {
        PyObject* values = PyList_New(static_cast<Py_ssize_t>(tags.size()));
        for (std::size_t index = 0; index < tags.size(); ++index) {
            PyList_SET_ITEM(values,
                            static_cast<Py_ssize_t>(index),
                            PyUnicode_FromString(tags[index].c_str()));
        }
        return values;
    };
    PyObject* inputGeometry = PyLong_FromLong(diagnostic->geometryId);
    PyObject* outputGeometry = PyLong_FromLong(diagnostic->geometryId);
    PyObject* oldDegree = PyLong_FromLong(diagnostic->oldDegree);
    PyObject* newDegree = PyLong_FromLong(diagnostic->newDegree);
    PyObject* retained = PyLong_FromLong(diagnostic->retainedInternalGeometryCount);
    PyObject* deleted = PyLong_FromLong(diagnostic->deletedInternalGeometryCount);
    PyObject* exposed = PyLong_FromLong(diagnostic->exposedInternalGeometryCount);
    PyObject* geometryTags = tagValues(after.geometryTags);
    PyObject* constraintTags = tagValues(after.constraintTags);
    PyObject* expressionValues = PyList_New(0);
    PyObject* receipt = mutationResult(before, sketch);
    PyDict_SetItemString(result, "input_geometry_index", inputGeometry);
    PyDict_SetItemString(result, "output_geometry_index", outputGeometry);
    PyDict_SetItemString(result, "old_degree", oldDegree);
    PyDict_SetItemString(result, "new_degree", newDegree);
    PyDict_SetItemString(result, "retained_internal_geometry_count", retained);
    PyDict_SetItemString(result, "deleted_internal_geometry_count", deleted);
    PyDict_SetItemString(result, "exposed_internal_geometry_count", exposed);
    PyDict_SetItemString(result, "geometry_tags", geometryTags);
    PyDict_SetItemString(result, "constraint_tags", constraintTags);
    PyDict_SetItemString(result, "expressions", expressionValues);
    PyDict_SetItemString(result, "mutation_receipt", receipt);
    Py_DECREF(inputGeometry);
    Py_DECREF(outputGeometry);
    Py_DECREF(oldDegree);
    Py_DECREF(newDegree);
    Py_DECREF(retained);
    Py_DECREF(deleted);
    Py_DECREF(exposed);
    Py_DECREF(geometryTags);
    Py_DECREF(constraintTags);
    Py_DECREF(expressionValues);
    Py_DECREF(receipt);
    return result;
}

PyObject* bsplineKnotMultiplicityDiagnosticResult(
    std::unique_ptr<BSplineKnotMultiplicityDiagnostic> diagnostic,
    const SketchMutationSnapshot& before,
    const char* operation
)
{
    if (!diagnostic || !diagnostic->sketch) {
        const std::string message = std::string(operation)
            + " diagnosis produced an invalid state.";
        PyErr_SetString(PyExc_RuntimeError, message.c_str());
        return nullptr;
    }
    auto* sketch = diagnostic->sketch.get();
    const auto after = mutationSnapshot(sketch);
    PyObject* result = solverDiagnosticResult(sketch, sketch->getLastDoF());
    addDiagnosticSketchState(result, sketch);
    addTranslateExternalState(result, sketch);

    auto tagValues = [](const std::vector<std::string>& tags) {
        PyObject* values = PyList_New(static_cast<Py_ssize_t>(tags.size()));
        for (std::size_t index = 0; index < tags.size(); ++index) {
            PyList_SET_ITEM(values,
                            static_cast<Py_ssize_t>(index),
                            PyUnicode_FromString(tags[index].c_str()));
        }
        return values;
    };
    PyObject* geometry = PyLong_FromLong(diagnostic->geometryId);
    PyObject* knot = PyLong_FromLong(diagnostic->knotIndex);
    PyObject* parameter = PyFloat_FromDouble(diagnostic->knotParameter);
    PyObject* degree = PyLong_FromLong(diagnostic->degree);
    PyObject* oldMultiplicity = PyLong_FromLong(diagnostic->oldMultiplicity);
    PyObject* newMultiplicity = PyLong_FromLong(diagnostic->newMultiplicity);
    PyObject* retained = PyLong_FromLong(diagnostic->retainedInternalGeometryCount);
    PyObject* deleted = PyLong_FromLong(diagnostic->deletedInternalGeometryCount);
    PyObject* exposed = PyLong_FromLong(diagnostic->exposedInternalGeometryCount);
    PyObject* geometryTags = tagValues(after.geometryTags);
    PyObject* constraintTags = tagValues(after.constraintTags);
    PyObject* expressionValues = PyList_New(0);
    PyObject* receipt = mutationResult(before, sketch);
    PyDict_SetItemString(result, "geometry_index", geometry);
    PyDict_SetItemString(result, "knot_index", knot);
    PyDict_SetItemString(result, "knot_parameter", parameter);
    PyDict_SetItemString(result, "degree", degree);
    PyDict_SetItemString(result, "old_multiplicity", oldMultiplicity);
    PyDict_SetItemString(result, "new_multiplicity", newMultiplicity);
    PyDict_SetItemString(result, "retained_internal_geometry_count", retained);
    PyDict_SetItemString(result, "deleted_internal_geometry_count", deleted);
    PyDict_SetItemString(result, "exposed_internal_geometry_count", exposed);
    PyDict_SetItemString(result, "geometry_tags", geometryTags);
    PyDict_SetItemString(result, "constraint_tags", constraintTags);
    PyDict_SetItemString(result, "expressions", expressionValues);
    PyDict_SetItemString(result, "mutation_receipt", receipt);
    Py_DECREF(geometry);
    Py_DECREF(knot);
    Py_DECREF(parameter);
    Py_DECREF(degree);
    Py_DECREF(oldMultiplicity);
    Py_DECREF(newMultiplicity);
    Py_DECREF(retained);
    Py_DECREF(deleted);
    Py_DECREF(exposed);
    Py_DECREF(geometryTags);
    Py_DECREF(constraintTags);
    Py_DECREF(expressionValues);
    Py_DECREF(receipt);
    return result;
}

PyObject* bsplineKnotInsertionDiagnosticResult(
    std::unique_ptr<BSplineKnotInsertionDiagnostic> diagnostic,
    const SketchMutationSnapshot& before
)
{
    if (!diagnostic || !diagnostic->sketch) {
        PyErr_SetString(PyExc_RuntimeError, "Insert Knot diagnosis produced an invalid state.");
        return nullptr;
    }
    auto* sketch = diagnostic->sketch.get();
    const auto after = mutationSnapshot(sketch);
    PyObject* result = solverDiagnosticResult(sketch, sketch->getLastDoF());
    addDiagnosticSketchState(result, sketch);
    addTranslateExternalState(result, sketch);

    auto tagValues = [](const std::vector<std::string>& tags) {
        PyObject* values = PyList_New(static_cast<Py_ssize_t>(tags.size()));
        for (std::size_t index = 0; index < tags.size(); ++index) {
            PyList_SET_ITEM(values,
                            static_cast<Py_ssize_t>(index),
                            PyUnicode_FromString(tags[index].c_str()));
        }
        return values;
    };
    PyObject* geometry = PyLong_FromLong(diagnostic->geometryId);
    PyObject* requestedParameter = PyFloat_FromDouble(diagnostic->requestedParameter);
    PyObject* knot = PyLong_FromLong(diagnostic->knotIndex);
    PyObject* parameter = PyFloat_FromDouble(diagnostic->knotParameter);
    PyObject* degree = PyLong_FromLong(diagnostic->degree);
    PyObject* oldMultiplicity = PyLong_FromLong(diagnostic->oldMultiplicity);
    PyObject* newMultiplicity = PyLong_FromLong(diagnostic->newMultiplicity);
    PyObject* retained = PyLong_FromLong(diagnostic->retainedInternalGeometryCount);
    PyObject* deleted = PyLong_FromLong(diagnostic->deletedInternalGeometryCount);
    PyObject* exposed = PyLong_FromLong(diagnostic->exposedInternalGeometryCount);
    PyObject* geometryTags = tagValues(after.geometryTags);
    PyObject* constraintTags = tagValues(after.constraintTags);
    PyObject* expressionValues = PyList_New(0);
    PyObject* receipt = mutationResult(before, sketch);
    PyDict_SetItemString(result, "geometry_index", geometry);
    PyDict_SetItemString(result, "requested_parameter", requestedParameter);
    PyDict_SetItemString(result, "knot_index", knot);
    PyDict_SetItemString(result, "knot_parameter", parameter);
    PyDict_SetItemString(result, "degree", degree);
    PyDict_SetItemString(result, "old_multiplicity", oldMultiplicity);
    PyDict_SetItemString(result, "new_multiplicity", newMultiplicity);
    PyDict_SetItemString(result, "retained_internal_geometry_count", retained);
    PyDict_SetItemString(result, "deleted_internal_geometry_count", deleted);
    PyDict_SetItemString(result, "exposed_internal_geometry_count", exposed);
    PyDict_SetItemString(result, "geometry_tags", geometryTags);
    PyDict_SetItemString(result, "constraint_tags", constraintTags);
    PyDict_SetItemString(result, "expressions", expressionValues);
    PyDict_SetItemString(result, "mutation_receipt", receipt);
    Py_DECREF(geometry);
    Py_DECREF(requestedParameter);
    Py_DECREF(knot);
    Py_DECREF(parameter);
    Py_DECREF(degree);
    Py_DECREF(oldMultiplicity);
    Py_DECREF(newMultiplicity);
    Py_DECREF(retained);
    Py_DECREF(deleted);
    Py_DECREF(exposed);
    Py_DECREF(geometryTags);
    Py_DECREF(constraintTags);
    Py_DECREF(expressionValues);
    Py_DECREF(receipt);
    return result;
}

bool bsplineKnotIndicesFromPython(PyObject* geometryObject,
                                  PyObject* knotObject,
                                  const char* operation,
                                  int& geometryId,
                                  int& knotIndex)
{
    if (!PyLong_Check(geometryObject) || PyBool_Check(geometryObject)
        || !PyLong_Check(knotObject) || PyBool_Check(knotObject)) {
        const std::string message = std::string(operation)
            + " geometry and knot indices must be integers.";
        PyErr_SetString(PyExc_TypeError, message.c_str());
        return false;
    }
    const long geometryValue = PyLong_AsLong(geometryObject);
    const long knotValue = PyLong_AsLong(knotObject);
    if (PyErr_Occurred() || geometryValue < 0
        || geometryValue > std::numeric_limits<int>::max() || knotValue < 0
        || knotValue > std::numeric_limits<int>::max()) {
        if (!PyErr_Occurred()) {
            const std::string message = std::string(operation) + " indices are out of bounds.";
            PyErr_SetString(PyExc_ValueError, message.c_str());
        }
        return false;
    }
    geometryId = static_cast<int>(geometryValue);
    knotIndex = static_cast<int>(knotValue);
    return true;
}

bool bsplineKnotInsertionTargetFromPython(PyObject* geometryObject,
                                          PyObject* parameterObject,
                                          int& geometryId,
                                          double& parameter)
{
    if (!PyLong_Check(geometryObject) || PyBool_Check(geometryObject)) {
        PyErr_SetString(PyExc_TypeError, "Insert Knot geometry index must be an integer.");
        return false;
    }
    const long geometryValue = PyLong_AsLong(geometryObject);
    if (PyErr_Occurred() || geometryValue < 0
        || geometryValue > std::numeric_limits<int>::max()) {
        if (!PyErr_Occurred()) {
            PyErr_SetString(PyExc_ValueError, "Insert Knot geometry index is out of bounds.");
        }
        return false;
    }
    if (PyBool_Check(parameterObject)
        || (!PyFloat_Check(parameterObject) && !PyLong_Check(parameterObject))) {
        PyErr_SetString(PyExc_TypeError, "Insert Knot parameter must be a finite number.");
        return false;
    }
    const double parameterValue = PyFloat_AsDouble(parameterObject);
    if (PyErr_Occurred() || !std::isfinite(parameterValue)) {
        if (!PyErr_Occurred()) {
            PyErr_SetString(PyExc_ValueError, "Insert Knot parameter must be finite.");
        }
        return false;
    }
    geometryId = static_cast<int>(geometryValue);
    parameter = parameterValue;
    return true;
}

bool joinCurvesArgumentsFromPython(PyObject* firstGeometryObject,
                                   PyObject* firstEndpointObject,
                                   PyObject* secondGeometryObject,
                                   PyObject* secondEndpointObject,
                                   int& firstGeometry,
                                   PointPos& firstEndpoint,
                                   int& secondGeometry,
                                   PointPos& secondEndpoint)
{
    const PyObject* values[] = {
        firstGeometryObject,
        firstEndpointObject,
        secondGeometryObject,
        secondEndpointObject,
    };
    for (const auto* value : values) {
        if (!PyLong_Check(value) || PyBool_Check(value)) {
            PyErr_SetString(PyExc_TypeError,
                            "Join Curves geometry indices and endpoints must be integers.");
            return false;
        }
    }
    const long firstGeometryValue = PyLong_AsLong(firstGeometryObject);
    const long firstEndpointValue = PyLong_AsLong(firstEndpointObject);
    const long secondGeometryValue = PyLong_AsLong(secondGeometryObject);
    const long secondEndpointValue = PyLong_AsLong(secondEndpointObject);
    if (PyErr_Occurred()) {
        return false;
    }
    if (firstGeometryValue < 0 || secondGeometryValue < 0
        || firstGeometryValue > std::numeric_limits<int>::max()
        || secondGeometryValue > std::numeric_limits<int>::max()) {
        PyErr_SetString(PyExc_ValueError,
                        "Join Curves geometry indices are out of bounds.");
        return false;
    }
    if ((firstEndpointValue != static_cast<long>(PointPos::start)
         && firstEndpointValue != static_cast<long>(PointPos::end))
        || (secondEndpointValue != static_cast<long>(PointPos::start)
            && secondEndpointValue != static_cast<long>(PointPos::end))) {
        PyErr_SetString(PyExc_ValueError,
                        "Join Curves endpoints must be 1 (start) or 2 (end).");
        return false;
    }
    firstGeometry = static_cast<int>(firstGeometryValue);
    firstEndpoint = static_cast<PointPos>(firstEndpointValue);
    secondGeometry = static_cast<int>(secondGeometryValue);
    secondEndpoint = static_cast<PointPos>(secondEndpointValue);
    return true;
}

bool constraintStateChangesFromPython(PyObject* object,
                                      const char* stateName,
                                      std::vector<std::pair<int, bool>>& changes)
{
    if (!PyList_Check(object) && !PyTuple_Check(object)) {
        const std::string message =
            std::string(stateName) + " changes must be a sequence of (constraint index, state).";
        PyErr_SetString(PyExc_TypeError, message.c_str());
        return false;
    }

    PyObject* sequence = PySequence_Fast(object, "changes must be a sequence.");
    if (!sequence) {
        return false;
    }
    const Py_ssize_t count = PySequence_Fast_GET_SIZE(sequence);
    if (count < 1 || count > 16) {
        Py_DECREF(sequence);
        PyErr_SetString(PyExc_ValueError, "changes must contain one through sixteen items.");
        return false;
    }

    changes.reserve(static_cast<std::size_t>(count));
    for (Py_ssize_t offset = 0; offset < count; ++offset) {
        PyObject* item = PySequence_Fast_GET_ITEM(sequence, offset);
        if ((!PyList_Check(item) && !PyTuple_Check(item)) || PySequence_Size(item) != 2) {
            Py_DECREF(sequence);
            const std::string message =
                std::string("every ") + stateName + " change must be an index/state pair.";
            PyErr_SetString(PyExc_TypeError, message.c_str());
            return false;
        }
        PyObject* indexObject = PySequence_GetItem(item, 0);
        PyObject* stateObject = PySequence_GetItem(item, 1);
        if (!indexObject || !stateObject) {
            Py_XDECREF(indexObject);
            Py_XDECREF(stateObject);
            Py_DECREF(sequence);
            return false;
        }
        if (!PyLong_Check(indexObject) || PyBool_Check(indexObject)
            || !PyBool_Check(stateObject)) {
            Py_DECREF(indexObject);
            Py_DECREF(stateObject);
            Py_DECREF(sequence);
            const std::string message =
                std::string(stateName) + " changes require an integer index and boolean state.";
            PyErr_SetString(PyExc_TypeError, message.c_str());
            return false;
        }
        const long index = PyLong_AsLong(indexObject);
        const bool state = stateObject == Py_True;
        Py_DECREF(indexObject);
        Py_DECREF(stateObject);
        if (PyErr_Occurred() || index < 0 || index > std::numeric_limits<int>::max()) {
            Py_DECREF(sequence);
            if (!PyErr_Occurred()) {
                const std::string message =
                    std::string(stateName) + " change index is outside the Sketch.";
                PyErr_SetString(PyExc_IndexError, message.c_str());
            }
            return false;
        }
        changes.emplace_back(static_cast<int>(index), state);
    }
    Py_DECREF(sequence);
    return true;
}

PyObject* constraintStateDiagnosticResult(SketchObject* sketch,
                                          const std::vector<std::pair<int, bool>>& changes,
                                          int degreesOfFreedom,
                                          const char* stateField)
{
    PyObject* result = solverDiagnosticResult(sketch, degreesOfFreedom);
    PyObject* indices = PyList_New(static_cast<Py_ssize_t>(changes.size()));
    PyObject* states = PyList_New(static_cast<Py_ssize_t>(changes.size()));
    for (std::size_t index = 0; index < changes.size(); ++index) {
        PyList_SET_ITEM(indices,
                        static_cast<Py_ssize_t>(index),
                        PyLong_FromLong(changes[index].first));
        PyList_SET_ITEM(states,
                        static_cast<Py_ssize_t>(index),
                        PyBool_FromLong(changes[index].second));
    }
    PyDict_SetItemString(result, "constraint_indices", indices);
    PyDict_SetItemString(result, stateField, states);
    Py_DECREF(indices);
    Py_DECREF(states);
    return result;
}

bool offsetEnumsFromPython(
    PyObject* joinObject,
    PyObject* sourceObject,
    OffsetJoinType& joinType,
    OffsetSourceMode& sourceMode
)
{
    if (!PyLong_Check(joinObject) || PyBool_Check(joinObject)
        || !PyLong_Check(sourceObject) || PyBool_Check(sourceObject)) {
        PyErr_SetString(PyExc_TypeError,
                        "Offset join type and source mode must be integers.");
        return false;
    }
    const long join = PyLong_AsLong(joinObject);
    const long source = PyLong_AsLong(sourceObject);
    if (PyErr_Occurred()) {
        return false;
    }
    if (join != static_cast<long>(OffsetJoinType::Arc)
        && join != static_cast<long>(OffsetJoinType::Intersection)) {
        PyErr_SetString(PyExc_ValueError,
                        "Offset join type must be 0 (Arc) or 2 (Intersection).");
        return false;
    }
    if (source != static_cast<long>(OffsetSourceMode::Keep)
        && source != static_cast<long>(OffsetSourceMode::Delete)
        && source != static_cast<long>(OffsetSourceMode::Constrain)) {
        PyErr_SetString(PyExc_ValueError,
                        "Offset source mode must be 0 (Keep), 1 (Delete), or 2 (Constrain).");
        return false;
    }
    joinType = static_cast<OffsetJoinType>(join);
    sourceMode = static_cast<OffsetSourceMode>(source);
    return true;
}

bool symmetryArgumentsFromPython(
    PyObject* referenceObject,
    PyObject* positionObject,
    PyObject* sourceObject,
    int& referenceGeometryId,
    PointPos& referencePosition,
    SymmetrySourceMode& sourceMode
)
{
    if (!PyLong_Check(referenceObject) || PyBool_Check(referenceObject)
        || !PyLong_Check(positionObject) || PyBool_Check(positionObject)
        || !PyLong_Check(sourceObject) || PyBool_Check(sourceObject)) {
        PyErr_SetString(PyExc_TypeError,
                        "Symmetry reference geometry, position, and source mode must be integers.");
        return false;
    }
    const long reference = PyLong_AsLong(referenceObject);
    const long position = PyLong_AsLong(positionObject);
    const long source = PyLong_AsLong(sourceObject);
    if (PyErr_Occurred()) {
        return false;
    }
    if (reference < std::numeric_limits<int>::min()
        || reference > std::numeric_limits<int>::max()) {
        PyErr_SetString(PyExc_OverflowError, "Symmetry reference geometry ID is out of range.");
        return false;
    }
    if (position < static_cast<long>(PointPos::none)
        || position > static_cast<long>(PointPos::mid)) {
        PyErr_SetString(PyExc_ValueError,
                        "Symmetry reference position must be 0 (whole), 1 (start), "
                        "2 (end), or 3 (center).");
        return false;
    }
    if (source != static_cast<long>(SymmetrySourceMode::Keep)
        && source != static_cast<long>(SymmetrySourceMode::Delete)
        && source != static_cast<long>(SymmetrySourceMode::Constrain)) {
        PyErr_SetString(PyExc_ValueError,
                        "Symmetry source mode must be 0 (Keep), 1 (Delete), or 2 (Constrain).");
        return false;
    }
    referenceGeometryId = static_cast<int>(reference);
    referencePosition = static_cast<PointPos>(position);
    sourceMode = static_cast<SymmetrySourceMode>(source);
    return true;
}
}  // namespace

// returns a string which represents the object e.g. when printed in python
std::string SketchObjectPy::representation() const
{
    return "<Sketcher::SketchObject>";
}

PyObject* SketchObjectPy::solve(PyObject* args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }
    int ret = this->getSketchObjectPtr()->solve();
    return Py_BuildValue("i", ret);
}

PyObject* SketchObjectPy::addGeometry(PyObject* args)
{
    PyObject* pcObj;
    PyObject* construction;  // this is an optional argument default false
    bool isConstruction;
    if (!PyArg_ParseTuple(args, "OO!", &pcObj, &PyBool_Type, &construction)) {
        PyErr_Clear();
        if (!PyArg_ParseTuple(args, "O", &pcObj)) {
            return nullptr;
        }
        else {
            isConstruction = false;
        }
    }
    else {
        isConstruction = Base::asBoolean(construction);
    }

    if (PyObject_TypeCheck(pcObj, &(Part::GeometryPy::Type))) {
        Part::Geometry* geo = static_cast<Part::GeometryPy*>(pcObj)->getGeometryPtr();
        int ret;
        // An arc created with Part.Arc will be converted into a Part.ArcOfCircle
        if (geo->is<Part::GeomTrimmedCurve>()) {
            Handle(Geom_TrimmedCurve) trim = Handle(Geom_TrimmedCurve)::DownCast(geo->handle());
            Handle(Geom_Circle) circle = Handle(Geom_Circle)::DownCast(trim->BasisCurve());
            Handle(Geom_Ellipse) ellipse = Handle(Geom_Ellipse)::DownCast(trim->BasisCurve());
            if (!circle.IsNull()) {
                // create the definition struct for that geom
                Part::GeomArcOfCircle aoc;
                aoc.setHandle(trim);
                ret = this->getSketchObjectPtr()->addGeometry(&aoc, isConstruction);
            }
            else if (!ellipse.IsNull()) {
                // create the definition struct for that geom
                Part::GeomArcOfEllipse aoe;
                aoe.setHandle(trim);
                ret = this->getSketchObjectPtr()->addGeometry(&aoe, isConstruction);
            }
            else {
                std::stringstream str;
                str << "Unsupported geometry type: " << geo->getTypeId().getName();
                PyErr_SetString(PyExc_TypeError, str.str().c_str());
                return nullptr;
            }
        }
        else if (
            geo->is<Part::GeomPoint>() || geo->is<Part::GeomCircle>() || geo->is<Part::GeomEllipse>()
            || geo->is<Part::GeomArcOfCircle>() || geo->is<Part::GeomArcOfEllipse>()
            || geo->is<Part::GeomArcOfHyperbola>() || geo->is<Part::GeomArcOfParabola>()
            || geo->is<Part::GeomBSplineCurve>() || geo->is<Part::GeomLineSegment>()
        ) {
            ret = this->getSketchObjectPtr()->addGeometry(geo, isConstruction);
        }
        else {
            std::stringstream str;
            str << "Unsupported geometry type: " << geo->getTypeId().getName();
            PyErr_SetString(PyExc_TypeError, str.str().c_str());
            return nullptr;
        }
        return Py::new_reference_to(Py::Long(ret));
    }
    else if (PyObject_TypeCheck(pcObj, &(PyList_Type)) || PyObject_TypeCheck(pcObj, &(PyTuple_Type))) {
        std::vector<Part::Geometry*> geoList;
        std::vector<std::shared_ptr<Part::Geometry>> tmpList;
        Py::Sequence list(pcObj);
        for (Py::Sequence::iterator it = list.begin(); it != list.end(); ++it) {
            if (PyObject_TypeCheck((*it).ptr(), &(Part::GeometryPy::Type))) {
                Part::Geometry* geo = static_cast<Part::GeometryPy*>((*it).ptr())->getGeometryPtr();

                // An arc created with Part.Arc will be converted into a Part.ArcOfCircle
                if (geo->is<Part::GeomTrimmedCurve>()) {
                    Handle(Geom_TrimmedCurve)
                        trim = Handle(Geom_TrimmedCurve)::DownCast(geo->handle());
                    Handle(Geom_Circle) circle = Handle(Geom_Circle)::DownCast(trim->BasisCurve());
                    Handle(Geom_Ellipse) ellipse = Handle(Geom_Ellipse)::DownCast(trim->BasisCurve());
                    if (!circle.IsNull()) {
                        // create the definition struct for that geom
                        std::shared_ptr<Part::GeomArcOfCircle> aoc(new Part::GeomArcOfCircle());
                        aoc->setHandle(trim);
                        geoList.push_back(aoc.get());
                        tmpList.push_back(aoc);
                    }
                    else if (!ellipse.IsNull()) {
                        // create the definition struct for that geom
                        std::shared_ptr<Part::GeomArcOfEllipse> aoe(new Part::GeomArcOfEllipse());
                        aoe->setHandle(trim);
                        geoList.push_back(aoe.get());
                        tmpList.push_back(aoe);
                    }
                    else {
                        std::stringstream str;
                        str << "Unsupported geometry type: " << geo->getTypeId().getName();
                        PyErr_SetString(PyExc_TypeError, str.str().c_str());
                        return nullptr;
                    }
                }
                else if (
                    geo->is<Part::GeomPoint>() || geo->is<Part::GeomCircle>()
                    || geo->is<Part::GeomEllipse>() || geo->is<Part::GeomArcOfCircle>()
                    || geo->is<Part::GeomArcOfEllipse>() || geo->is<Part::GeomArcOfHyperbola>()
                    || geo->is<Part::GeomArcOfParabola>() || geo->is<Part::GeomBSplineCurve>()
                    || geo->is<Part::GeomLineSegment>()
                ) {
                    geoList.push_back(geo);
                }
                else {
                    std::stringstream str;
                    str << "Unsupported geometry type: " << geo->getTypeId().getName();
                    PyErr_SetString(PyExc_TypeError, str.str().c_str());
                    return nullptr;
                }
            }
        }

        int ret = this->getSketchObjectPtr()->addGeometry(geoList, isConstruction) + 1;
        std::size_t numGeo = geoList.size();
        Py::Tuple tuple(numGeo);
        for (std::size_t i = 0; i < numGeo; ++i) {
            int geoId = ret - int(numGeo - i);
            tuple.setItem(i, Py::Long(geoId));
        }

        return Py::new_reference_to(tuple);
    }

    std::string error = std::string("type must be 'Geometry' or list of 'Geometry', not ");
    error += pcObj->ob_type->tp_name;
    throw Py::TypeError(error);
}

PyObject* SketchObjectPy::delGeometry(PyObject* args)
{
    int Index;
    PyObject* noSolve = Py_False;
    if (!PyArg_ParseTuple(args, "i|O!", &Index, &PyBool_Type, &noSolve)) {
        return nullptr;
    }

    auto* sketch = this->getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    if (sketch->delGeometry(
            Index,
            Base::asBoolean(noSolve) ? DeleteOption::NoSolve : DeleteOption::UpdateGeometry
        )) {
        std::stringstream str;
        str << "Not able to delete a geometry with the given index: " << Index;
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    return mutationResult(before, sketch);
}

PyObject* SketchObjectPy::delGeometries(PyObject* args)
{
    PyObject* pcObj;
    PyObject* noSolve = Py_False;
    if (!PyArg_ParseTuple(args, "O|O!", &pcObj, &PyBool_Type, &noSolve)) {
        return nullptr;
    }

    if (PyObject_TypeCheck(pcObj, &(PyList_Type)) || PyObject_TypeCheck(pcObj, &(PyTuple_Type))) {

        std::vector<int> geoIdList;
        Py::Sequence list(pcObj);
        for (Py::Sequence::iterator it = list.begin(); it != list.end(); ++it) {
            if (PyLong_Check((*it).ptr())) {
                geoIdList.push_back(PyLong_AsLong((*it).ptr()));
            }
        }

        auto* sketch = this->getSketchObjectPtr();
        const auto before = mutationSnapshot(sketch);
        if (sketch->delGeometries(
                geoIdList,
                Base::asBoolean(noSolve) ? DeleteOption::NoSolve : DeleteOption::UpdateGeometry
            )) {
            std::stringstream str;
            str << "Not able to delete geometries";
            PyErr_SetString(PyExc_ValueError, str.str().c_str());
            return nullptr;
        }

        return mutationResult(before, sketch);
    }

    std::string error = std::string("type must be list of GeoIds, not ");
    error += pcObj->ob_type->tp_name;
    throw Py::TypeError(error);
}

PyObject* SketchObjectPy::deleteAllGeometry(PyObject* args)
{
    PyObject* noSolve = Py_False;
    if (!PyArg_ParseTuple(args, "|O!", &PyBool_Type, &noSolve)) {
        return nullptr;
    }

    auto* sketch = this->getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    if (sketch->deleteAllGeometry(
            Base::asBoolean(noSolve) ? DeleteOption::NoSolve : DeleteOption::UpdateGeometry
        )) {
        std::stringstream str;
        str << "Unable to delete Geometry";
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    return mutationResult(before, sketch);
}

PyObject* SketchObjectPy::captureMutationState()
{
    auto* sketch = this->getSketchObjectPtr();
    auto* document = sketch ? sketch->getDocument() : nullptr;
    if (
        !sketch || !document || !sketch->getNameInDocument()
        || document->getBookedTransactionID() == App::NullTransaction
        || !document->hasPendingTransaction()
    ) {
        throw Py::RuntimeError("The Sketch has no exact active mutation transaction");
    }
    auto* state = new SketchMutationState(sketch);
    PyObject* capsule = PyCapsule_New(
        state,
        mutationStateCapsuleName,
        deleteMutationStateCapsule
    );
    if (!capsule) {
        delete state;
        throw Py::Exception();
    }
    return capsule;
}

PyObject* SketchObjectPy::restoreMutationState(PyObject* args)
{
    PyObject* capsule = nullptr;
    if (!PyArg_ParseTuple(args, "O", &capsule)) {
        return nullptr;
    }
    auto* state = static_cast<SketchMutationState*>(
        PyCapsule_GetPointer(capsule, mutationStateCapsuleName)
    );
    if (!state) {
        throw Py::TypeError("state must be an exact Sketch mutation savepoint");
    }
    auto* sketch = this->getSketchObjectPtr();
    if (state->restored) {
        throw Py::RuntimeError("This Sketch mutation savepoint was already restored");
    }
    if (
        !sketch || sketch != state->owner || sketch->getDocument() != state->document
        || !sketch->getNameInDocument() || state->objectName != sketch->getNameInDocument()
        || !state->document->hasPendingTransaction()
        || state->document->getBookedTransactionID() != state->transactionId
    ) {
        throw Py::RuntimeError("The exact Sketch mutation transaction is no longer available");
    }

    sketch->setStatus(App::ObjectStatus::Restore, true);
    try {
        sketch->ExpressionEngine.Paste(*state->expressionEngine);
        sketch->Constraints.Paste(*state->constraints);
        sketch->Geometry.Paste(*state->geometry);
        sketch->ExternalTypes.Paste(*state->externalTypes);
        sketch->ExternalGeometry.Paste(*state->externalGeometry);
        sketch->ExternalGeo.Paste(*state->externalGeo);
        sketch->Exports.Paste(*state->exports);
    }
    catch (...) {
        sketch->setStatus(App::ObjectStatus::Restore, false);
        throw;
    }
    sketch->setStatus(App::ObjectStatus::Restore, false);
    sketch->onSketchRestore();
    state->restored = true;
    Py_RETURN_NONE;
}

PyObject* SketchObjectPy::detectDegeneratedGeometries(PyObject* args)
{
    double tolerance {};
    if (!PyArg_ParseTuple(args, "d", &tolerance)) {
        return nullptr;
    }

    SketchAnalysis analyse(this->getSketchObjectPtr());
    int count = analyse.detectDegeneratedGeometries(tolerance);
    return Py::new_reference_to(Py::Long(count));
}

PyObject* SketchObjectPy::removeDegeneratedGeometries(PyObject* args)
{
    double tolerance {};
    if (!PyArg_ParseTuple(args, "d", &tolerance)) {
        return nullptr;
    }

    SketchAnalysis analyse(this->getSketchObjectPtr());
    int count = analyse.removeDegeneratedGeometries(tolerance);
    return Py::new_reference_to(Py::Long(count));
}

PyObject* SketchObjectPy::deleteAllConstraints(PyObject* args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }

    if (this->getSketchObjectPtr()->deleteAllConstraints()) {
        std::stringstream str;
        str << "Unable to delete Constraints";
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    Py_Return;
}


PyObject* SketchObjectPy::toggleConstruction(PyObject* args)
{
    int Index;
    if (!PyArg_ParseTuple(args, "i", &Index)) {
        return nullptr;
    }

    if (this->getSketchObjectPtr()->toggleConstruction(Index)) {
        std::stringstream str;
        str << "Not able to toggle a geometry with the given index: " << Index;
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    Py_Return;
}

PyObject* SketchObjectPy::setConstruction(PyObject* args)
{
    int Index;
    PyObject* Mode;
    if (!PyArg_ParseTuple(args, "iO!", &Index, &PyBool_Type, &Mode)) {
        return nullptr;
    }

    if (this->getSketchObjectPtr()->setConstruction(Index, Base::asBoolean(Mode))) {
        std::stringstream str;
        str << "Not able to set construction mode of a geometry with the given index: " << Index;
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    Py_Return;
}

PyObject* SketchObjectPy::getConstruction(PyObject* args)
{
    int Index;
    if (!PyArg_ParseTuple(args, "i", &Index)) {
        return nullptr;
    }

    auto gf = this->getSketchObjectPtr()->getGeometryFacade(Index);

    if (gf) {
        return Py::new_reference_to(Py::Boolean(gf->getConstruction()));
    }

    std::stringstream str;
    str << "Not able to retrieve construction mode of a geometry with the given index: " << Index;
    PyErr_SetString(PyExc_ValueError, str.str().c_str());
    return nullptr;
}

PyObject* SketchObjectPy::addConstraint(PyObject* args)
{
    PyObject* pcObj;
    if (!PyArg_ParseTuple(args, "O", &pcObj)) {
        return nullptr;
    }

    if (PyObject_TypeCheck(pcObj, &(Sketcher::ConstraintPy::Type))) {
        Sketcher::Constraint* constr = static_cast<Sketcher::ConstraintPy*>(pcObj)->getConstraintPtr();
        if (!this->getSketchObjectPtr()->evaluateConstraint(constr)) {
            PyErr_SetString(PyExc_IndexError, "Constraint has invalid indexes");
            return nullptr;
        }
        int ret = this->getSketchObjectPtr()->addConstraint(constr);
        // this solve is necessary because:
        // 1. The addition of constraint is part of a command addition
        // 2. This solve happens before the command is committed
        // 3. A constraint, may effect a geometry change (think of coincident,
        // a line's point moves to meet the other line's point
        // 4. The transaction is committed before any other solve, for example
        // the one of execute() triggered by a recompute (UpdateActive) is generated.
        // 5. Upon "undo", the constraint is removed (it was before the command was committed)
        //    however, the geometry changed after the command was committed, so the point that
        //    moved do not go back to the position where it was.
        //
        // N.B.: However, the solve itself may be inhibited in cases where groups of
        // geometry/constraints
        //      are added together, because in that case undoing will also make the geometry
        //      disappear.
        this->getSketchObjectPtr()->solve();
        // if the geometry moved during the solve, then the initial solution is invalid
        // at this point, so a point movement may not work in cases where redundant constraints
        // exist. this forces recalculation of the initial solution (not a full solve)
        if (this->getSketchObjectPtr()->noRecomputes) {
            this->getSketchObjectPtr()->setUpSketch();
            this->getSketchObjectPtr()->Constraints.touch();  // update solver information
        }
        return Py::new_reference_to(Py::Long(ret));
    }
    else if (PyObject_TypeCheck(pcObj, &(PyList_Type)) || PyObject_TypeCheck(pcObj, &(PyTuple_Type))) {
        std::vector<Constraint*> values;
        Py::Sequence list(pcObj);
        for (Py::Sequence::iterator it = list.begin(); it != list.end(); ++it) {
            if (PyObject_TypeCheck((*it).ptr(), &(ConstraintPy::Type))) {
                Constraint* con = static_cast<ConstraintPy*>((*it).ptr())->getConstraintPtr();
                values.push_back(con);
            }
        }

        for (std::vector<Constraint*>::iterator it = values.begin(); it != values.end(); ++it) {
            if (!this->getSketchObjectPtr()->evaluateConstraint(*it)) {
                PyErr_SetString(
                    PyExc_IndexError,
                    QT_TRANSLATE_NOOP(
                        "Notifications",
                        "The constraint has invalid index information and is malformed."
                    )
                );
                return nullptr;
            }
        }
        int ret = getSketchObjectPtr()->addConstraints(values) + 1;
        std::size_t numCon = values.size();
        Py::Tuple tuple(numCon);
        for (std::size_t i = 0; i < numCon; ++i) {
            int conId = ret - int(numCon - i);
            tuple.setItem(i, Py::Long(conId));
        }
        return Py::new_reference_to(tuple);
    }

    std::string error = std::string("type must be 'Constraint' or list of 'Constraint', not ");
    error += pcObj->ob_type->tp_name;
    throw Py::TypeError(error);
}

PyObject* SketchObjectPy::diagnoseAdditionalConstraints(PyObject* args)
{
    PyObject* pcObj;
    if (!PyArg_ParseTuple(args, "O", &pcObj)) {
        return nullptr;
    }

    std::vector<Constraint*> values;
    if (!proposedConstraintsFromPython(pcObj, values)) {
        return nullptr;
    }

    auto* sketch = getSketchObjectPtr();
    for (auto* constraint : values) {
        if (!sketch->evaluateConstraint(constraint)) {
            PyErr_SetString(PyExc_IndexError,
                            "A proposed constraint has invalid index information.");
            return nullptr;
        }
    }

    const int firstProposedIndex = sketch->Constraints.getSize();
    const int degreesOfFreedom = sketch->diagnoseAdditionalConstraints(values);
    return constraintDiagnosticResult(
        sketch, firstProposedIndex, values.size(), degreesOfFreedom);
}

PyObject* SketchObjectPy::diagnoseBlockConstraints(PyObject* args)
{
    PyObject* pcObj;
    if (!PyArg_ParseTuple(args, "O", &pcObj)) {
        return nullptr;
    }

    std::vector<Constraint*> values;
    if (!proposedConstraintsFromPython(pcObj, values)) {
        return nullptr;
    }

    auto* sketch = getSketchObjectPtr();
    for (const auto* constraint : values) {
        if (constraint->Type != Block || constraint->FirstPos != PointPos::none) {
            PyErr_SetString(PyExc_TypeError,
                            "Every constraint must be an exact whole-geometry Block constraint.");
            return nullptr;
        }
        if (!sketch->evaluateConstraint(constraint) || constraint->First < 0) {
            PyErr_SetString(PyExc_IndexError,
                            "A Block constraint has invalid internal geometry information.");
            return nullptr;
        }
    }

    const int firstProposedIndex = sketch->Constraints.getSize();
    const int degreesOfFreedom = sketch->diagnoseBlockConstraints(values);
    return constraintDiagnosticResult(
        sketch, firstProposedIndex, values.size(), degreesOfFreedom);
}

PyObject* SketchObjectPy::diagnoseConstraintReplacement(PyObject* args)
{
    int replacedConstraintIndex;
    PyObject* pcObj;
    if (!PyArg_ParseTuple(args, "iO", &replacedConstraintIndex, &pcObj)) {
        return nullptr;
    }

    auto* sketch = getSketchObjectPtr();
    const int constraintCount = sketch->Constraints.getSize();
    if (replacedConstraintIndex < 0 || replacedConstraintIndex >= constraintCount) {
        PyErr_SetString(PyExc_IndexError,
                        "replacement constraint index is outside the active Sketch.");
        return nullptr;
    }

    std::vector<Constraint*> values;
    if (!proposedConstraintsFromPython(pcObj, values)) {
        return nullptr;
    }
    for (auto* constraint : values) {
        if (!sketch->evaluateConstraint(constraint)) {
            PyErr_SetString(PyExc_IndexError,
                            "A replacement constraint has invalid index information.");
            return nullptr;
        }
    }

    const int firstProposedIndex = constraintCount - 1;
    const int degreesOfFreedom =
        sketch->diagnoseConstraintReplacement(replacedConstraintIndex, values);
    return constraintDiagnosticResult(
        sketch, firstProposedIndex, values.size(), degreesOfFreedom);
}

PyObject* SketchObjectPy::diagnoseDrivingChanges(PyObject* args)
{
    PyObject* changesObject;
    if (!PyArg_ParseTuple(args, "O", &changesObject)) {
        return nullptr;
    }
    std::vector<std::pair<int, bool>> changes;
    if (!constraintStateChangesFromPython(changesObject, "driving", changes)) {
        return nullptr;
    }

    auto* sketch = getSketchObjectPtr();
    const int degreesOfFreedom = sketch->diagnoseDrivingChanges(changes);
    if (degreesOfFreedom == std::numeric_limits<int>::min()) {
        PyErr_SetString(PyExc_ValueError, "one or more driving changes are invalid.");
        return nullptr;
    }
    return constraintStateDiagnosticResult(
        sketch, changes, degreesOfFreedom, "driving_states");
}

PyObject* SketchObjectPy::diagnoseActiveChanges(PyObject* args)
{
    PyObject* changesObject;
    if (!PyArg_ParseTuple(args, "O", &changesObject)) {
        return nullptr;
    }
    std::vector<std::pair<int, bool>> changes;
    if (!constraintStateChangesFromPython(changesObject, "active", changes)) {
        return nullptr;
    }

    auto* sketch = getSketchObjectPtr();
    const int degreesOfFreedom = sketch->diagnoseActiveChanges(changes);
    if (degreesOfFreedom == std::numeric_limits<int>::min()) {
        PyErr_SetString(PyExc_ValueError, "one or more active changes are invalid.");
        return nullptr;
    }
    return constraintStateDiagnosticResult(
        sketch, changes, degreesOfFreedom, "active_states");
}

PyObject* SketchObjectPy::diagnoseFillet(PyObject* args)
{
    int geoId1;
    int geoId2;
    PyObject* point1;
    PyObject* point2;
    PyObject* preserveCorner;
    auto* sketch = getSketchObjectPtr();

    if (PyArg_ParseTuple(args,
                         "iiO!O!O!",
                         &geoId1,
                         &geoId2,
                         &(Base::VectorPy::Type),
                         &point1,
                         &(Base::VectorPy::Type),
                         &point2,
                         &PyBool_Type,
                         &preserveCorner)) {
        const Base::Vector3d refPnt1 = static_cast<Base::VectorPy*>(point1)->value();
        const Base::Vector3d refPnt2 = static_cast<Base::VectorPy*>(point2)->value();
        auto diagnostic = sketch->diagnoseFillet(
            geoId1,
            geoId2,
            refPnt1,
            refPnt2,
            Base::asBoolean(preserveCorner)
        );
        if (!diagnostic) {
            PyErr_SetString(PyExc_ValueError, "The exact curve-pair Fillet is unavailable.");
            return nullptr;
        }
        const bool trimmed = !GeometryFacade::getBlocked(sketch->getGeometry(geoId1))
            && !GeometryFacade::getBlocked(sketch->getGeometry(geoId2));
        return filletDiagnosticResult(
            std::move(diagnostic), {geoId1, geoId2}, "curve_pair", sketch->Geometry.getSize(), trimmed);
    }

    PyErr_Clear();
    int posId;
    if (PyArg_ParseTuple(args, "iiO!", &geoId1, &posId, &PyBool_Type, &preserveCorner)) {
        auto diagnostic = sketch->diagnoseFillet(
            geoId1,
            static_cast<PointPos>(posId),
            Base::asBoolean(preserveCorner)
        );
        if (!diagnostic) {
            PyErr_SetString(PyExc_ValueError, "The exact corner Fillet is unavailable.");
            return nullptr;
        }

        std::vector<int> geometryIds;
        std::vector<PointPos> positionIds;
        sketch->getDirectlyCoincidentPoints(
            geoId1, static_cast<PointPos>(posId), geometryIds, positionIds);
        geometryIds = sketch->chooseFilletsEdges(geometryIds);
        if (geometryIds.size() != 2) {
            PyErr_SetString(PyExc_RuntimeError, "Corner Fillet diagnosis lost its exact inputs.");
            return nullptr;
        }
        const bool trimmed = !GeometryFacade::getBlocked(sketch->getGeometry(geometryIds[0]))
            && !GeometryFacade::getBlocked(sketch->getGeometry(geometryIds[1]));
        return filletDiagnosticResult(
            std::move(diagnostic), geometryIds, "corner", sketch->Geometry.getSize(), trimmed);
    }

    PyErr_SetString(
        PyExc_TypeError,
        "diagnoseFillet() accepts (int, int, bool) or (int, int, Vector, Vector, bool)."
    );
    return nullptr;
}

PyObject* SketchObjectPy::diagnoseChamfer(PyObject* args)
{
    int geoId1;
    int geoId2;
    PyObject* point1;
    PyObject* point2;
    PyObject* preserveCorner;
    auto* sketch = getSketchObjectPtr();

    if (PyArg_ParseTuple(args,
                         "iiO!O!O!",
                         &geoId1,
                         &geoId2,
                         &(Base::VectorPy::Type),
                         &point1,
                         &(Base::VectorPy::Type),
                         &point2,
                         &PyBool_Type,
                         &preserveCorner)) {
        const Base::Vector3d refPnt1 = static_cast<Base::VectorPy*>(point1)->value();
        const Base::Vector3d refPnt2 = static_cast<Base::VectorPy*>(point2)->value();
        auto diagnostic = sketch->diagnoseChamfer(
            geoId1,
            geoId2,
            refPnt1,
            refPnt2,
            Base::asBoolean(preserveCorner)
        );
        if (!diagnostic) {
            PyErr_SetString(PyExc_ValueError, "The exact curve-pair Chamfer is unavailable.");
            return nullptr;
        }
        const bool trimmed = !GeometryFacade::getBlocked(sketch->getGeometry(geoId1))
            && !GeometryFacade::getBlocked(sketch->getGeometry(geoId2));
        return chamferDiagnosticResult(
            std::move(diagnostic),
            {geoId1, geoId2},
            "curve_pair",
            sketch->Geometry.getSize(),
            trimmed
        );
    }

    PyErr_Clear();
    int posId;
    if (PyArg_ParseTuple(args, "iiO!", &geoId1, &posId, &PyBool_Type, &preserveCorner)) {
        auto diagnostic = sketch->diagnoseChamfer(
            geoId1,
            static_cast<PointPos>(posId),
            Base::asBoolean(preserveCorner)
        );
        if (!diagnostic) {
            PyErr_SetString(PyExc_ValueError, "The exact corner Chamfer is unavailable.");
            return nullptr;
        }

        std::vector<int> geometryIds;
        std::vector<PointPos> positionIds;
        sketch->getDirectlyCoincidentPoints(
            geoId1, static_cast<PointPos>(posId), geometryIds, positionIds);
        geometryIds = sketch->chooseFilletsEdges(geometryIds);
        if (geometryIds.size() != 2) {
            PyErr_SetString(PyExc_RuntimeError, "Corner Chamfer diagnosis lost its exact inputs.");
            return nullptr;
        }
        const bool trimmed = !GeometryFacade::getBlocked(sketch->getGeometry(geometryIds[0]))
            && !GeometryFacade::getBlocked(sketch->getGeometry(geometryIds[1]));
        return chamferDiagnosticResult(
            std::move(diagnostic),
            geometryIds,
            "corner",
            sketch->Geometry.getSize(),
            trimmed
        );
    }

    PyErr_SetString(
        PyExc_TypeError,
        "diagnoseChamfer() accepts (int, int, bool) or (int, int, Vector, Vector, bool)."
    );
    return nullptr;
}

PyObject* SketchObjectPy::diagnoseTrim(PyObject* args)
{
    int geoId;
    PyObject* point;
    if (!PyArg_ParseTuple(args, "iO!", &geoId, &(Base::VectorPy::Type), &point)) {
        return nullptr;
    }
    const Base::Vector3d referencePoint = static_cast<Base::VectorPy*>(point)->value();
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    auto diagnostic = sketch->diagnoseTrim(geoId, referencePoint);
    if (!diagnostic) {
        PyErr_SetString(PyExc_ValueError, "The exact human Trim target is unavailable.");
        return nullptr;
    }
    return curvePointMutationDiagnosticResult(
        "Trim", std::move(diagnostic), before, geoId, referencePoint);
}

PyObject* SketchObjectPy::diagnoseSplit(PyObject* args)
{
    int geoId;
    PyObject* point;
    if (!PyArg_ParseTuple(args, "iO!", &geoId, &(Base::VectorPy::Type), &point)) {
        return nullptr;
    }
    const Base::Vector3d referencePoint = static_cast<Base::VectorPy*>(point)->value();
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    auto diagnostic = sketch->diagnoseSplit(geoId, referencePoint);
    if (!diagnostic) {
        PyErr_SetString(PyExc_ValueError, "The exact human Split target is unavailable.");
        return nullptr;
    }
    return curvePointMutationDiagnosticResult(
        "Split", std::move(diagnostic), before, geoId, referencePoint);
}

PyObject* SketchObjectPy::diagnoseExtend(PyObject* args)
{
    int geoId;
    PyObject* point;
    int endpointValue;
    if (!PyArg_ParseTuple(
            args, "iO!i", &geoId, &(Base::VectorPy::Type), &point, &endpointValue)) {
        return nullptr;
    }
    const Base::Vector3d targetPoint = static_cast<Base::VectorPy*>(point)->value();
    const auto endpoint = static_cast<Sketcher::PointPos>(endpointValue);
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    double increment = 0.0;
    auto diagnostic = sketch->diagnoseExtend(geoId, targetPoint, endpoint, increment);
    if (!diagnostic) {
        PyErr_SetString(PyExc_ValueError, "The exact human Extend target is unavailable.");
        return nullptr;
    }
    PyObject* result = curvePointMutationDiagnosticResult(
        "Extend", std::move(diagnostic), before, geoId, targetPoint);
    if (!result) {
        return nullptr;
    }
    PyObject* endpointObject = PyUnicode_FromString(
        endpoint == Sketcher::PointPos::start ? "start" : "end");
    PyObject* incrementObject = PyFloat_FromDouble(increment);
    PyDict_SetItemString(result, "input_endpoint", endpointObject);
    PyDict_SetItemString(result, "extension_increment", incrementObject);
    Py_DECREF(endpointObject);
    Py_DECREF(incrementObject);
    return result;
}

PyObject* SketchObjectPy::diagnoseExternal(PyObject* args)
{
    char* objectName = nullptr;
    char* subName = nullptr;
    PyObject* defining = Py_False;
    PyObject* intersection = Py_False;
    if (!PyArg_ParseTuple(
            args,
            "ss|O!O!",
            &objectName,
            &subName,
            &PyBool_Type,
            &defining,
            &PyBool_Type,
            &intersection
        )) {
        return nullptr;
    }

    auto* sketch = getSketchObjectPtr();
    auto* document = sketch->getDocument();
    auto* source = document ? document->getObject(objectName) : nullptr;
    if (!source) {
        PyErr_SetString(PyExc_ValueError,
                        "The external-geometry source does not exist in this document.");
        return nullptr;
    }
    auto diagnostic = sketch->diagnoseExternal(
        source,
        subName,
        Base::asBoolean(defining),
        Base::asBoolean(intersection)
    );
    if (!diagnostic) {
        PyErr_SetString(PyExc_ValueError,
                        "The exact external-geometry reference is unavailable.");
        return nullptr;
    }
    return externalGeometryDiagnosticResult(
        std::move(diagnostic),
        objectName,
        subName,
        Base::asBoolean(defining),
        Base::asBoolean(intersection)
    );
}

PyObject* SketchObjectPy::delConstraint(PyObject* args)
{
    int Index;
    PyObject* noSolve = Py_False;

    if (!PyArg_ParseTuple(args, "i|O!", &Index, &PyBool_Type, &noSolve)) {
        return nullptr;
    }

    auto* sketch = this->getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    if (sketch->delConstraint(
            Index,
            Base::asBoolean(noSolve) ? DeleteOption::NoSolve : DeleteOption::UpdateGeometry
        )) {
        std::stringstream str;
        str << "Not able to delete a constraint with the given index: " << Index;
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    return mutationResult(before, sketch);
}
PyObject* SketchObjectPy::delConstraints(PyObject* args)
{
    PyObject* pcObj;
    PyObject* updateGeometry = Py_True;
    PyObject* noSolve = Py_False;

    if (
        !PyArg_ParseTuple(args, "O|O!O!", &pcObj, &PyBool_Type, &updateGeometry, &PyBool_Type, &noSolve)
    ) {
        return nullptr;
    }

    if (PyObject_TypeCheck(pcObj, &(PyList_Type)) || PyObject_TypeCheck(pcObj, &(PyTuple_Type))) {

        std::vector<int> constraintIdList;
        Py::Sequence list(pcObj);
        for (Py::Sequence::iterator it = list.begin(); it != list.end(); ++it) {
            if (PyLong_Check((*it).ptr())) {
                constraintIdList.push_back(PyLong_AsLong((*it).ptr()));
            }
        }

        auto* sketch = this->getSketchObjectPtr();
        const auto before = mutationSnapshot(sketch);
        if (sketch->delConstraints(
                constraintIdList,
                (Base::asBoolean(updateGeometry) ? DeleteOption::UpdateGeometry : DeleteOption::NoFlag)
                    | (Base::asBoolean(noSolve) ? DeleteOption::NoSolve : DeleteOption::NoFlag)
            )
            == -1) {
            std::stringstream str;
            str << "Not able to delete constraints, invalid indices";
            PyErr_SetString(PyExc_ValueError, str.str().c_str());
            return nullptr;
        }

        return mutationResult(before, sketch);
    }

    std::string error = std::string("type must be list of constraint indices (int), not ");
    error += pcObj->ob_type->tp_name;
    throw Py::TypeError(error);

    Py_Return;
}

PyObject* SketchObjectPy::renameConstraint(PyObject* args)
{
    int Index;
    char* utf8Name;
    if (!PyArg_ParseTuple(args, "iet", &Index, "utf-8", &utf8Name)) {
        return nullptr;
    }

    std::string Name = utf8Name;
    PyMem_Free(utf8Name);

    if (this->getSketchObjectPtr()->Constraints.getSize() <= Index) {
        std::stringstream str;
        str << "Not able to rename a constraint with the given index: " << Index;
        PyErr_SetString(PyExc_IndexError, str.str().c_str());
        return nullptr;
    }

    if (!Name.empty()) {

        if (!Sketcher::PropertyConstraintList::validConstraintName(Name)) {
            std::stringstream str;
            str << "Invalid constraint name with the given index: " << Index;
            PyErr_SetString(PyExc_IndexError, str.str().c_str());
            return nullptr;
        }

        const std::vector<Sketcher::Constraint*>& vals = getSketchObjectPtr()->Constraints.getValues();
        for (std::size_t i = 0; i < vals.size(); ++i) {
            if (static_cast<int>(i) != Index && Name == vals[i]->Name) {
                PyErr_SetString(PyExc_ValueError, "Duplicate constraint not allowed");
                return nullptr;
            }
        }
    }

    this->getSketchObjectPtr()->renameConstraint(Index, Name);

    Py_Return;
}

PyObject* SketchObjectPy::getIndexByName(PyObject* args) const
{
    char* utf8Name;
    if (!PyArg_ParseTuple(args, "et", "utf-8", &utf8Name)) {
        return nullptr;
    }

    std::string Name = utf8Name;
    PyMem_Free(utf8Name);

    if (Name.empty()) {
        PyErr_SetString(PyExc_ValueError, "Passed string is empty");
        return nullptr;
    }

    const std::vector<Sketcher::Constraint*>& vals = getSketchObjectPtr()->Constraints.getValues();
    for (std::size_t i = 0; i < vals.size(); ++i) {
        if (Name == vals[i]->Name) {
            return Py_BuildValue("i", i);
        }
    }

    PyErr_SetString(PyExc_LookupError, "No such constraint found");
    return nullptr;
}

PyObject* SketchObjectPy::setAllowUnaligned(PyObject* args)
{
    PyObject* allowObj;
    if (!PyArg_ParseTuple(args, "O!", &PyBool_Type, &allowObj)) {
        return nullptr;
    }
    bool allow = Base::asBoolean(allowObj);
    this->getSketchObjectPtr()->setAllowUnaligned(allow);

    Py_Return;
}

PyObject* SketchObjectPy::diagnoseCarbonCopy(PyObject* args)
{
    char* objectName = nullptr;
    PyObject* construction = nullptr;
    PyObject* allowOtherBody = nullptr;
    PyObject* allowUnaligned = nullptr;
    if (!PyArg_ParseTuple(args,
                          "sO!O!O!",
                          &objectName,
                          &PyBool_Type,
                          &construction,
                          &PyBool_Type,
                          &allowOtherBody,
                          &PyBool_Type,
                          &allowUnaligned)) {
        return nullptr;
    }
    auto* sketch = getSketchObjectPtr();
    auto* document = sketch->getDocument();
    auto* source = document ? document->getObject(objectName) : nullptr;
    if (!source) {
        PyErr_SetString(PyExc_ValueError,
                        "The Carbon Copy source does not exist in this document.");
        return nullptr;
    }
    const auto before = mutationSnapshot(sketch);
    auto diagnostic = sketch->diagnoseCarbonCopy(source,
                                                 Base::asBoolean(construction),
                                                 Base::asBoolean(allowOtherBody),
                                                 Base::asBoolean(allowUnaligned));
    if (!diagnostic) {
        PyErr_SetString(PyExc_ValueError,
                        "The exact human Carbon Copy source is unavailable.");
        return nullptr;
    }
    return carbonCopyDiagnosticResult(std::move(diagnostic),
                                      before,
                                      objectName,
                                      Base::asBoolean(construction),
                                      Base::asBoolean(allowOtherBody),
                                      Base::asBoolean(allowUnaligned));
}

PyObject* SketchObjectPy::carbonCopyExact(PyObject* args)
{
    char* objectName = nullptr;
    PyObject* construction = nullptr;
    PyObject* allowOtherBody = nullptr;
    PyObject* allowUnaligned = nullptr;
    if (!PyArg_ParseTuple(args,
                          "sO!O!O!",
                          &objectName,
                          &PyBool_Type,
                          &construction,
                          &PyBool_Type,
                          &allowOtherBody,
                          &PyBool_Type,
                          &allowUnaligned)) {
        return nullptr;
    }
    auto* sketch = getSketchObjectPtr();
    auto* document = sketch->getDocument();
    auto* source = document ? document->getObject(objectName) : nullptr;
    if (!source) {
        PyErr_SetString(PyExc_ValueError,
                        "The Carbon Copy source does not exist in this document.");
        return nullptr;
    }
    const auto before = mutationSnapshot(sketch);
    if (sketch->carbonCopyExact(source,
                               Base::asBoolean(construction),
                               Base::asBoolean(allowOtherBody),
                               Base::asBoolean(allowUnaligned)) < 0) {
        PyErr_SetString(PyExc_ValueError,
                        "Sketcher rejected the exact Carbon Copy operation.");
        return nullptr;
    }
    return mutationResult(before, sketch);
}

PyObject* SketchObjectPy::diagnoseTranslate(PyObject* args)
{
    PyObject* geometryObject = nullptr;
    PyObject* firstVectorObject = nullptr;
    PyObject* secondVectorObject = nullptr;
    PyObject* equalizeObject = nullptr;
    int copyCount = 0;
    int rowCount = 0;
    if (!PyArg_ParseTuple(args,
                          "OO!iO!iO!",
                          &geometryObject,
                          &Base::VectorPy::Type,
                          &firstVectorObject,
                          &copyCount,
                          &Base::VectorPy::Type,
                          &secondVectorObject,
                          &rowCount,
                          &PyBool_Type,
                          &equalizeObject)) {
        return nullptr;
    }
    std::vector<int> geometryIds;
    if (!geometryIdsFromPython(geometryObject, geometryIds, "Translate")) {
        return nullptr;
    }
    const auto firstVector = static_cast<Base::VectorPy*>(firstVectorObject)->value();
    const auto secondVector = static_cast<Base::VectorPy*>(secondVectorObject)->value();
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    auto diagnostic = sketch->diagnoseTranslate(geometryIds,
                                                firstVector,
                                                copyCount,
                                                secondVector,
                                                rowCount,
                                                Base::asBoolean(equalizeObject));
    if (!diagnostic) {
        PyErr_SetString(PyExc_ValueError,
                        "The exact human Translate operation is unavailable.");
        return nullptr;
    }
    return translateDiagnosticResult(std::move(diagnostic), before);
}

PyObject* SketchObjectPy::translateExact(PyObject* args)
{
    PyObject* geometryObject = nullptr;
    PyObject* firstVectorObject = nullptr;
    PyObject* secondVectorObject = nullptr;
    PyObject* equalizeObject = nullptr;
    int copyCount = 0;
    int rowCount = 0;
    if (!PyArg_ParseTuple(args,
                          "OO!iO!iO!",
                          &geometryObject,
                          &Base::VectorPy::Type,
                          &firstVectorObject,
                          &copyCount,
                          &Base::VectorPy::Type,
                          &secondVectorObject,
                          &rowCount,
                          &PyBool_Type,
                          &equalizeObject)) {
        return nullptr;
    }
    std::vector<int> geometryIds;
    if (!geometryIdsFromPython(geometryObject, geometryIds, "Translate")) {
        return nullptr;
    }
    const auto firstVector = static_cast<Base::VectorPy*>(firstVectorObject)->value();
    const auto secondVector = static_cast<Base::VectorPy*>(secondVectorObject)->value();
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    if (sketch->translateExact(geometryIds,
                               firstVector,
                               copyCount,
                               secondVector,
                               rowCount,
                               Base::asBoolean(equalizeObject)) < 0) {
        PyErr_SetString(PyExc_ValueError, "Sketcher rejected the exact Translate operation.");
        return nullptr;
    }
    return mutationResult(before, sketch);
}

PyObject* SketchObjectPy::diagnoseRotate(PyObject* args)
{
    PyObject* geometryObject = nullptr;
    PyObject* centerObject = nullptr;
    PyObject* equalizeObject = nullptr;
    double totalAngleRadians = 0.0;
    int copyCount = 0;
    if (!PyArg_ParseTuple(args,
                          "OO!diO!",
                          &geometryObject,
                          &Base::VectorPy::Type,
                          &centerObject,
                          &totalAngleRadians,
                          &copyCount,
                          &PyBool_Type,
                          &equalizeObject)) {
        return nullptr;
    }
    std::vector<int> geometryIds;
    if (!geometryIdsFromPython(geometryObject, geometryIds, "Rotate")) {
        return nullptr;
    }
    const auto center = static_cast<Base::VectorPy*>(centerObject)->value();
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    auto diagnostic = sketch->diagnoseRotate(geometryIds,
                                             center,
                                             totalAngleRadians,
                                             copyCount,
                                             Base::asBoolean(equalizeObject));
    if (!diagnostic) {
        PyErr_SetString(PyExc_ValueError,
                        "The exact human Rotate operation is unavailable.");
        return nullptr;
    }
    return rotateDiagnosticResult(std::move(diagnostic), before);
}

PyObject* SketchObjectPy::rotateExact(PyObject* args)
{
    PyObject* geometryObject = nullptr;
    PyObject* centerObject = nullptr;
    PyObject* equalizeObject = nullptr;
    double totalAngleRadians = 0.0;
    int copyCount = 0;
    if (!PyArg_ParseTuple(args,
                          "OO!diO!",
                          &geometryObject,
                          &Base::VectorPy::Type,
                          &centerObject,
                          &totalAngleRadians,
                          &copyCount,
                          &PyBool_Type,
                          &equalizeObject)) {
        return nullptr;
    }
    std::vector<int> geometryIds;
    if (!geometryIdsFromPython(geometryObject, geometryIds, "Rotate")) {
        return nullptr;
    }
    const auto center = static_cast<Base::VectorPy*>(centerObject)->value();
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    if (sketch->rotateExact(geometryIds,
                            center,
                            totalAngleRadians,
                            copyCount,
                            Base::asBoolean(equalizeObject)) < 0) {
        PyErr_SetString(PyExc_ValueError, "Sketcher rejected the exact Rotate operation.");
        return nullptr;
    }
    return mutationResult(before, sketch);
}

PyObject* SketchObjectPy::diagnoseScale(PyObject* args)
{
    PyObject* geometryObject = nullptr;
    PyObject* centerObject = nullptr;
    PyObject* keepOriginalsObject = nullptr;
    PyObject* allowOriginConstraintsObject = nullptr;
    double scaleFactor = 0.0;
    if (!PyArg_ParseTuple(args,
                          "OO!dO!O!",
                          &geometryObject,
                          &Base::VectorPy::Type,
                          &centerObject,
                          &scaleFactor,
                          &PyBool_Type,
                          &keepOriginalsObject,
                          &PyBool_Type,
                          &allowOriginConstraintsObject)) {
        return nullptr;
    }
    std::vector<int> geometryIds;
    if (!geometryIdsFromPython(geometryObject, geometryIds, "Scale")) {
        return nullptr;
    }
    const auto center = static_cast<Base::VectorPy*>(centerObject)->value();
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    auto diagnostic = sketch->diagnoseScale(geometryIds,
                                            center,
                                            scaleFactor,
                                            Base::asBoolean(keepOriginalsObject),
                                            Base::asBoolean(allowOriginConstraintsObject));
    if (!diagnostic) {
        PyErr_SetString(PyExc_ValueError,
                        "The exact human Scale operation is unavailable.");
        return nullptr;
    }
    return scaleDiagnosticResult(std::move(diagnostic), before);
}

PyObject* SketchObjectPy::scaleExact(PyObject* args)
{
    PyObject* geometryObject = nullptr;
    PyObject* centerObject = nullptr;
    PyObject* keepOriginalsObject = nullptr;
    PyObject* allowOriginConstraintsObject = nullptr;
    double scaleFactor = 0.0;
    if (!PyArg_ParseTuple(args,
                          "OO!dO!O!",
                          &geometryObject,
                          &Base::VectorPy::Type,
                          &centerObject,
                          &scaleFactor,
                          &PyBool_Type,
                          &keepOriginalsObject,
                          &PyBool_Type,
                          &allowOriginConstraintsObject)) {
        return nullptr;
    }
    std::vector<int> geometryIds;
    if (!geometryIdsFromPython(geometryObject, geometryIds, "Scale")) {
        return nullptr;
    }
    const auto center = static_cast<Base::VectorPy*>(centerObject)->value();
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    if (sketch->scaleExact(geometryIds,
                           center,
                           scaleFactor,
                           Base::asBoolean(keepOriginalsObject),
                           Base::asBoolean(allowOriginConstraintsObject)) < 0) {
        PyErr_SetString(PyExc_ValueError, "Sketcher rejected the exact Scale operation.");
        return nullptr;
    }
    return mutationResult(before, sketch);
}

PyObject* SketchObjectPy::diagnoseOffset(PyObject* args)
{
    PyObject* geometryObject = nullptr;
    PyObject* joinObject = nullptr;
    PyObject* sourceObject = nullptr;
    double offsetLength = 0.0;
    if (!PyArg_ParseTuple(
            args,
            "OdOO",
            &geometryObject,
            &offsetLength,
            &joinObject,
            &sourceObject
        )) {
        return nullptr;
    }
    std::vector<int> geometryIds;
    OffsetJoinType joinType;
    OffsetSourceMode sourceMode;
    if (!geometryIdsFromPython(geometryObject, geometryIds, "Offset")
        || !offsetEnumsFromPython(joinObject, sourceObject, joinType, sourceMode)) {
        return nullptr;
    }
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    auto diagnostic = sketch->diagnoseOffset(
        geometryIds,
        offsetLength,
        joinType,
        sourceMode
    );
    if (!diagnostic) {
        PyErr_SetString(PyExc_ValueError,
                        "The exact human Offset operation is unavailable.");
        return nullptr;
    }
    return offsetDiagnosticResult(std::move(diagnostic), before);
}

PyObject* SketchObjectPy::offsetExact(PyObject* args)
{
    PyObject* geometryObject = nullptr;
    PyObject* joinObject = nullptr;
    PyObject* sourceObject = nullptr;
    double offsetLength = 0.0;
    if (!PyArg_ParseTuple(
            args,
            "OdOO",
            &geometryObject,
            &offsetLength,
            &joinObject,
            &sourceObject
        )) {
        return nullptr;
    }
    std::vector<int> geometryIds;
    OffsetJoinType joinType;
    OffsetSourceMode sourceMode;
    if (!geometryIdsFromPython(geometryObject, geometryIds, "Offset")
        || !offsetEnumsFromPython(joinObject, sourceObject, joinType, sourceMode)) {
        return nullptr;
    }
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    if (sketch->offsetExact(geometryIds, offsetLength, joinType, sourceMode) < 0) {
        PyErr_SetString(PyExc_ValueError, "Sketcher rejected the exact Offset operation.");
        return nullptr;
    }
    return mutationResult(before, sketch);
}

PyObject* SketchObjectPy::diagnoseSymmetry(PyObject* args)
{
    PyObject* geometryObject = nullptr;
    PyObject* referenceObject = nullptr;
    PyObject* positionObject = nullptr;
    PyObject* sourceObject = nullptr;
    if (!PyArg_ParseTuple(
            args,
            "OOOO",
            &geometryObject,
            &referenceObject,
            &positionObject,
            &sourceObject
        )) {
        return nullptr;
    }
    std::vector<int> geometryIds;
    int referenceGeometryId = GeoEnum::GeoUndef;
    PointPos referencePosition = PointPos::none;
    SymmetrySourceMode sourceMode = SymmetrySourceMode::Keep;
    if (!geometryIdsFromPython(geometryObject, geometryIds, "Symmetry")
        || !symmetryArgumentsFromPython(referenceObject,
                                        positionObject,
                                        sourceObject,
                                        referenceGeometryId,
                                        referencePosition,
                                        sourceMode)) {
        return nullptr;
    }
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    auto diagnostic = sketch->diagnoseSymmetry(
        geometryIds,
        referenceGeometryId,
        referencePosition,
        sourceMode
    );
    if (!diagnostic) {
        PyErr_SetString(PyExc_ValueError,
                        "The exact human Symmetry operation is unavailable.");
        return nullptr;
    }
    return symmetryDiagnosticResult(std::move(diagnostic), before);
}

PyObject* SketchObjectPy::symmetryExact(PyObject* args)
{
    PyObject* geometryObject = nullptr;
    PyObject* referenceObject = nullptr;
    PyObject* positionObject = nullptr;
    PyObject* sourceObject = nullptr;
    if (!PyArg_ParseTuple(
            args,
            "OOOO",
            &geometryObject,
            &referenceObject,
            &positionObject,
            &sourceObject
        )) {
        return nullptr;
    }
    std::vector<int> geometryIds;
    int referenceGeometryId = GeoEnum::GeoUndef;
    PointPos referencePosition = PointPos::none;
    SymmetrySourceMode sourceMode = SymmetrySourceMode::Keep;
    if (!geometryIdsFromPython(geometryObject, geometryIds, "Symmetry")
        || !symmetryArgumentsFromPython(referenceObject,
                                        positionObject,
                                        sourceObject,
                                        referenceGeometryId,
                                        referencePosition,
                                        sourceMode)) {
        return nullptr;
    }
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    if (sketch->symmetryExact(
            geometryIds,
            referenceGeometryId,
            referencePosition,
            sourceMode
        ) < 0) {
        PyErr_SetString(PyExc_ValueError, "Sketcher rejected the exact Symmetry operation.");
        return nullptr;
    }
    return mutationResult(before, sketch);
}

PyObject* SketchObjectPy::diagnoseRemoveAxesAlignment(PyObject* args)
{
    PyObject* geometryObject = nullptr;
    if (!PyArg_ParseTuple(args, "O", &geometryObject)) {
        return nullptr;
    }
    std::vector<int> geometryIds;
    if (!geometryIdsFromPython(geometryObject, geometryIds, "Remove Axes Alignment")) {
        return nullptr;
    }
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    auto diagnostic = sketch->diagnoseRemoveAxesAlignment(geometryIds);
    if (!diagnostic) {
        PyErr_SetString(PyExc_ValueError,
                        "The exact human Remove Axes Alignment operation is unavailable.");
        return nullptr;
    }
    return axisAlignmentRemovalDiagnosticResult(std::move(diagnostic), before);
}

PyObject* SketchObjectPy::removeAxesAlignmentExact(PyObject* args)
{
    PyObject* geometryObject = nullptr;
    if (!PyArg_ParseTuple(args, "O", &geometryObject)) {
        return nullptr;
    }
    std::vector<int> geometryIds;
    if (!geometryIdsFromPython(geometryObject, geometryIds, "Remove Axes Alignment")) {
        return nullptr;
    }
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    if (sketch->removeAxesAlignmentExact(geometryIds) <= 0) {
        PyErr_SetString(PyExc_ValueError,
                        "Sketcher rejected the exact Remove Axes Alignment operation.");
        return nullptr;
    }
    return mutationResult(before, sketch);
}

PyObject* SketchObjectPy::diagnoseConvertToNURBS(PyObject* args)
{
    PyObject* geometryObject = nullptr;
    if (!PyArg_ParseTuple(args, "O", &geometryObject)) {
        return nullptr;
    }
    std::vector<int> geometryIds;
    if (!geometryIdsFromPython(geometryObject, geometryIds, "Geometry-to-B-Spline")) {
        return nullptr;
    }
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    auto diagnostic = sketch->diagnoseConvertToNURBS(geometryIds);
    if (!diagnostic) {
        PyErr_SetString(PyExc_ValueError,
                        "The exact Geometry-to-B-Spline operation is unavailable.");
        return nullptr;
    }
    return nurbsConversionDiagnosticResult(std::move(diagnostic), before);
}

PyObject* SketchObjectPy::convertToNURBSExact(PyObject* args)
{
    PyObject* geometryObject = nullptr;
    if (!PyArg_ParseTuple(args, "O", &geometryObject)) {
        return nullptr;
    }
    std::vector<int> geometryIds;
    if (!geometryIdsFromPython(geometryObject, geometryIds, "Geometry-to-B-Spline")) {
        return nullptr;
    }
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    if (sketch->convertToNURBSExact(geometryIds) <= 0) {
        PyErr_SetString(PyExc_ValueError,
                        "Sketcher rejected the exact Geometry-to-B-Spline operation.");
        return nullptr;
    }
    return mutationResult(before, sketch);
}

PyObject* SketchObjectPy::diagnoseIncreaseBSplineDegree(PyObject* args)
{
    PyObject* geometryObject = nullptr;
    if (!PyArg_ParseTuple(args, "O", &geometryObject)) {
        return nullptr;
    }
    std::vector<int> geometryIds;
    if (!geometryIdsFromPython(geometryObject, geometryIds, "Increase B-Spline Degree")) {
        return nullptr;
    }
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    auto diagnostic = sketch->diagnoseIncreaseBSplineDegree(geometryIds);
    if (!diagnostic) {
        PyErr_SetString(PyExc_ValueError,
                        "The exact Increase B-Spline Degree operation is unavailable.");
        return nullptr;
    }
    return bsplineDegreeIncreaseDiagnosticResult(std::move(diagnostic), before);
}

PyObject* SketchObjectPy::increaseBSplineDegreeExact(PyObject* args)
{
    PyObject* geometryObject = nullptr;
    if (!PyArg_ParseTuple(args, "O", &geometryObject)) {
        return nullptr;
    }
    std::vector<int> geometryIds;
    if (!geometryIdsFromPython(geometryObject, geometryIds, "Increase B-Spline Degree")) {
        return nullptr;
    }
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    if (sketch->increaseBSplineDegreeExact(geometryIds) <= 0) {
        PyErr_SetString(PyExc_ValueError,
                        "Sketcher rejected the exact Increase B-Spline Degree operation.");
        return nullptr;
    }
    return mutationResult(before, sketch);
}

PyObject* SketchObjectPy::diagnoseDecreaseBSplineDegree(PyObject* args)
{
    PyObject* geometryObject = nullptr;
    if (!PyArg_ParseTuple(args, "O", &geometryObject)) {
        return nullptr;
    }
    if (!PyLong_Check(geometryObject) || PyBool_Check(geometryObject)) {
        PyErr_SetString(PyExc_TypeError,
                        "Decrease B-Spline Degree geometry ID must be an integer.");
        return nullptr;
    }
    const long geometryId = PyLong_AsLong(geometryObject);
    if (PyErr_Occurred() || geometryId < 0 || geometryId > std::numeric_limits<int>::max()) {
        if (!PyErr_Occurred()) {
            PyErr_SetString(PyExc_ValueError,
                            "Decrease B-Spline Degree geometry ID is out of bounds.");
        }
        return nullptr;
    }
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    auto diagnostic = sketch->diagnoseDecreaseBSplineDegree(static_cast<int>(geometryId));
    if (!diagnostic) {
        PyErr_SetString(PyExc_ValueError,
                        "The exact Decrease B-Spline Degree operation is unavailable.");
        return nullptr;
    }
    return bsplineDegreeDecreaseDiagnosticResult(std::move(diagnostic), before);
}

PyObject* SketchObjectPy::decreaseBSplineDegreeExact(PyObject* args)
{
    PyObject* geometryObject = nullptr;
    if (!PyArg_ParseTuple(args, "O", &geometryObject)) {
        return nullptr;
    }
    if (!PyLong_Check(geometryObject) || PyBool_Check(geometryObject)) {
        PyErr_SetString(PyExc_TypeError,
                        "Decrease B-Spline Degree geometry ID must be an integer.");
        return nullptr;
    }
    const long geometryId = PyLong_AsLong(geometryObject);
    if (PyErr_Occurred() || geometryId < 0 || geometryId > std::numeric_limits<int>::max()) {
        if (!PyErr_Occurred()) {
            PyErr_SetString(PyExc_ValueError,
                            "Decrease B-Spline Degree geometry ID is out of bounds.");
        }
        return nullptr;
    }
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    if (sketch->decreaseBSplineDegreeExact(static_cast<int>(geometryId)) <= 0) {
        PyErr_SetString(PyExc_ValueError,
                        "Sketcher rejected the exact Decrease B-Spline Degree operation.");
        return nullptr;
    }
    return mutationResult(before, sketch);
}

PyObject* SketchObjectPy::diagnoseIncreaseBSplineKnotMultiplicity(PyObject* args)
{
    PyObject* geometryObject = nullptr;
    PyObject* knotObject = nullptr;
    if (!PyArg_ParseTuple(args, "OO", &geometryObject, &knotObject)) {
        return nullptr;
    }
    int geometryId = -1;
    int knotIndex = -1;
    if (!bsplineKnotIndicesFromPython(
            geometryObject,
            knotObject,
            "Increase Knot Multiplicity",
            geometryId,
            knotIndex
        )) {
        return nullptr;
    }
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    auto diagnostic = sketch->diagnoseIncreaseBSplineKnotMultiplicity(geometryId, knotIndex);
    if (!diagnostic) {
        PyErr_SetString(PyExc_ValueError,
                        "The exact Increase Knot Multiplicity operation is unavailable.");
        return nullptr;
    }
    return bsplineKnotMultiplicityDiagnosticResult(
        std::move(diagnostic),
        before,
        "Increase Knot Multiplicity"
    );
}

PyObject* SketchObjectPy::increaseBSplineKnotMultiplicityExact(PyObject* args)
{
    PyObject* geometryObject = nullptr;
    PyObject* knotObject = nullptr;
    if (!PyArg_ParseTuple(args, "OO", &geometryObject, &knotObject)) {
        return nullptr;
    }
    int geometryId = -1;
    int knotIndex = -1;
    if (!bsplineKnotIndicesFromPython(
            geometryObject,
            knotObject,
            "Increase Knot Multiplicity",
            geometryId,
            knotIndex
        )) {
        return nullptr;
    }
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    if (sketch->increaseBSplineKnotMultiplicityExact(geometryId, knotIndex) <= 0) {
        PyErr_SetString(PyExc_ValueError,
                        "Sketcher rejected the exact Increase Knot Multiplicity operation.");
        return nullptr;
    }
    return mutationResult(before, sketch);
}

PyObject* SketchObjectPy::diagnoseDecreaseBSplineKnotMultiplicity(PyObject* args)
{
    PyObject* geometryObject = nullptr;
    PyObject* knotObject = nullptr;
    if (!PyArg_ParseTuple(args, "OO", &geometryObject, &knotObject)) {
        return nullptr;
    }
    int geometryId = -1;
    int knotIndex = -1;
    if (!bsplineKnotIndicesFromPython(
            geometryObject,
            knotObject,
            "Decrease Knot Multiplicity",
            geometryId,
            knotIndex
        )) {
        return nullptr;
    }
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    auto diagnostic = sketch->diagnoseDecreaseBSplineKnotMultiplicity(geometryId, knotIndex);
    if (!diagnostic) {
        PyErr_SetString(PyExc_ValueError,
                        "The exact Decrease Knot Multiplicity operation is unavailable.");
        return nullptr;
    }
    return bsplineKnotMultiplicityDiagnosticResult(
        std::move(diagnostic),
        before,
        "Decrease Knot Multiplicity"
    );
}

PyObject* SketchObjectPy::decreaseBSplineKnotMultiplicityExact(PyObject* args)
{
    PyObject* geometryObject = nullptr;
    PyObject* knotObject = nullptr;
    if (!PyArg_ParseTuple(args, "OO", &geometryObject, &knotObject)) {
        return nullptr;
    }
    int geometryId = -1;
    int knotIndex = -1;
    if (!bsplineKnotIndicesFromPython(
            geometryObject,
            knotObject,
            "Decrease Knot Multiplicity",
            geometryId,
            knotIndex
        )) {
        return nullptr;
    }
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    if (sketch->decreaseBSplineKnotMultiplicityExact(geometryId, knotIndex) <= 0) {
        PyErr_SetString(PyExc_ValueError,
                        "Sketcher rejected the exact Decrease Knot Multiplicity operation.");
        return nullptr;
    }
    return mutationResult(before, sketch);
}

PyObject* SketchObjectPy::diagnoseInsertBSplineKnot(PyObject* args)
{
    PyObject* geometryObject = nullptr;
    PyObject* parameterObject = nullptr;
    if (!PyArg_ParseTuple(args, "OO", &geometryObject, &parameterObject)) {
        return nullptr;
    }
    int geometryId = -1;
    double parameter = 0.0;
    if (!bsplineKnotInsertionTargetFromPython(
            geometryObject,
            parameterObject,
            geometryId,
            parameter
        )) {
        return nullptr;
    }
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    auto diagnostic = sketch->diagnoseInsertBSplineKnot(geometryId, parameter);
    if (!diagnostic) {
        PyErr_SetString(PyExc_ValueError, "The exact Insert Knot operation is unavailable.");
        return nullptr;
    }
    return bsplineKnotInsertionDiagnosticResult(std::move(diagnostic), before);
}

PyObject* SketchObjectPy::insertBSplineKnotExact(PyObject* args)
{
    PyObject* geometryObject = nullptr;
    PyObject* parameterObject = nullptr;
    if (!PyArg_ParseTuple(args, "OO", &geometryObject, &parameterObject)) {
        return nullptr;
    }
    int geometryId = -1;
    double parameter = 0.0;
    if (!bsplineKnotInsertionTargetFromPython(
            geometryObject,
            parameterObject,
            geometryId,
            parameter
        )) {
        return nullptr;
    }
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    if (sketch->insertBSplineKnotExact(geometryId, parameter) <= 0) {
        PyErr_SetString(PyExc_ValueError, "Sketcher rejected the exact Insert Knot operation.");
        return nullptr;
    }
    return mutationResult(before, sketch);
}

PyObject* SketchObjectPy::carbonCopy(PyObject* args)
{
    char* ObjectName;
    PyObject* construction = Py_True;
    if (!PyArg_ParseTuple(args, "s|O!", &ObjectName, &PyBool_Type, &construction)) {
        return nullptr;
    }

    Sketcher::SketchObject* skObj = this->getSketchObjectPtr();
    App::DocumentObject* Obj = skObj->getDocument()->getObject(ObjectName);

    if (!Obj) {
        std::stringstream str;
        str << ObjectName << " does not exist in the document";
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    bool xinv = false, yinv = false;
    if (!skObj->isCarbonCopyAllowed(Obj->getDocument(), Obj, xinv, yinv)) {
        std::stringstream str;
        str << ObjectName << " is not allowed for a carbon copy operation in this sketch";
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    if (skObj->carbonCopy(Obj, Base::asBoolean(construction)) < 0) {
        std::stringstream str;
        str << "Not able to add the requested geometry";
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    Py_Return;
}

PyObject* SketchObjectPy::addExternal(PyObject* args)
{
    char* ObjectName = nullptr;
    char* SubName = nullptr;
    PyObject* defining = Py_False;
    PyObject* intersection = Py_False;
    if (!PyArg_ParseTuple(
            args,
            "ss|O!O!",
            &ObjectName,
            &SubName,
            &PyBool_Type,
            &defining,
            &PyBool_Type,
            &intersection
        )) {
        return nullptr;
    }

    bool isDefining = Base::asBoolean(defining);
    bool isIntersection = Base::asBoolean(intersection);

    // get the target object for the external link
    Sketcher::SketchObject* skObj = this->getSketchObjectPtr();
    App::DocumentObject* Obj = skObj->getDocument()->getObject(ObjectName);
    if (!Obj) {
        std::stringstream str;
        str << ObjectName << " does not exist in the document";
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }
    // check if this type of external geometry is allowed
    if (!skObj->isExternalAllowed(Obj->getDocument(), Obj)) {
        std::stringstream str;
        str << ObjectName << " is not allowed as external geometry of this sketch";
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    // add the external
    if (skObj->addExternal(Obj, SubName, isDefining, isIntersection) < 0) {
        std::stringstream str;
        str << "Not able to add external shape element " << SubName;
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    Py_Return;
}

PyObject* SketchObjectPy::delExternal(PyObject* args)
{
    int Index;
    if (!PyArg_ParseTuple(args, "i", &Index)) {
        return nullptr;
    }

    if (this->getSketchObjectPtr()->delExternal(Index)) {
        std::stringstream str;
        str << "Not able to delete an external geometry with the given index: " << Index;
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    Py_Return;
}

PyObject* SketchObjectPy::delExternals(PyObject* args)
{
    PyObject* pcObj;
    if (!PyArg_ParseTuple(args, "O", &pcObj)) {
        return nullptr;
    }

    if (PyObject_TypeCheck(pcObj, &(PyList_Type)) || PyObject_TypeCheck(pcObj, &(PyTuple_Type))) {
        std::vector<int> extGeoIdList;
        Py::Sequence list(pcObj);
        for (const auto& item : list) {
            if (!PyLong_Check(item.ptr())) {
                throw Py::TypeError("list elements must be int");
            }
            extGeoIdList.push_back(PyLong_AsLong(item.ptr()));
        }

        if (this->getSketchObjectPtr()->delExternal(extGeoIdList)) {
            std::stringstream str;
            str << "Not able to delete external geometries";
            PyErr_SetString(PyExc_ValueError, str.str().c_str());
            return nullptr;
        }

        Py_Return;
    }

    std::string error = std::string("type must be list of External GeoIds, not ");
    error += pcObj->ob_type->tp_name;
    throw Py::TypeError(error);
}

PyObject* SketchObjectPy::delConstraintOnPoint(PyObject* args)
{
    int Index, pos = -1;
    if (!PyArg_ParseTuple(args, "i|i", &Index, &pos)) {
        return nullptr;
    }

    if (pos >= static_cast<int>(Sketcher::PointPos::none)
        && pos <= static_cast<int>(Sketcher::PointPos::mid)) {
        // This is the whole range of valid positions
        if (this->getSketchObjectPtr()
                ->delConstraintOnPoint(Index, static_cast<Sketcher::PointPos>(pos))) {
            std::stringstream str;
            str << "Not able to delete a constraint on point with the given index: " << Index
                << " and position: " << pos;
            PyErr_SetString(PyExc_ValueError, str.str().c_str());
            return nullptr;
        }
    }
    else if (pos == -1) {
        if (this->getSketchObjectPtr()->delConstraintOnPoint(Index)) {
            std::stringstream str;
            str << "Not able to delete a constraint on point with the given index: " << Index;
            PyErr_SetString(PyExc_ValueError, str.str().c_str());
            return nullptr;
        }
    }
    else {
        PyErr_SetString(PyExc_ValueError, "Wrong PointPos argument");
        return nullptr;
    }

    Py_Return;
}

PyObject* SketchObjectPy::delConstraintsToExternal()
{
    this->getSketchObjectPtr()->delConstraintsToExternal();
    Py_Return;
}

PyObject* SketchObjectPy::setTextAndFont(PyObject* args, PyObject* /*kwd*/)
{
    int constrIndex = -1;
    char* textStr;
    char* fontStr;
    PyObject* isHeightObj = Py_True;
    PyObject* isConstrObj = Py_False;  // Default to null (parameter not provided)

    // "iss|O!O!" (int, str, str, | bool, bool)
    if (!PyArg_ParseTuple(
            args,
            "iss|O!O!",
            &constrIndex,
            &textStr,
            &fontStr,
            &PyBool_Type,
            &isHeightObj,
            &PyBool_Type,
            &isConstrObj
        )) {
        return nullptr;
    }

    std::string text(textStr);
    std::string font(fontStr);

    // Call the C++ implementation
    int err = this->getSketchObjectPtr()->setTextAndFont(
        constrIndex,
        text,
        font,
        Base::asBoolean(isHeightObj),
        Base::asBoolean(isConstrObj)
    );

    // Handle errors returned from the C++ function
    if (err) {
        std::stringstream str;
        if (err == -1) {
            str << "Invalid constraint index or not a Text constraint: " << constrIndex;
        }
        else if (err == -6) {
            str << "Cannot set text/font because of invalid geometry in the sketch";
        }
        else {  // Generic error for solver failures etc.
            str << "Failed to set text/font for constraint with index " << constrIndex
                << ". The operation would result in an invalid sketch.";
        }
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    Py_Return;
}

PyObject* SketchObjectPy::setDatum(PyObject* args)
{
    double Datum;
    int Index;
    PyObject* object;
    Base::Quantity Quantity;

    do {
        // handle (int,Quantity)
        if (PyArg_ParseTuple(args, "iO!", &Index, &(Base::QuantityPy::Type), &object)) {
            Quantity = *(static_cast<Base::QuantityPy*>(object)->getQuantityPtr());
            if (Quantity.getUnit() == Base::Unit::Angle) {
                Datum = Base::toRadians<double>(Quantity.getValue());
                break;
            }
            else {
                Datum = Quantity.getValue();
                break;
            }
        }

        // handle (int,double)
        PyErr_Clear();
        if (PyArg_ParseTuple(args, "id", &Index, &Datum)) {
            Quantity.setValue(Datum);
            break;
        }

        // handle (string,Quantity)
        char* constrName;
        PyErr_Clear();
        if (PyArg_ParseTuple(args, "sO!", &constrName, &(Base::QuantityPy::Type), &object)) {
            Quantity = *(static_cast<Base::QuantityPy*>(object)->getQuantityPtr());
            if (Quantity.getUnit() == Base::Unit::Angle) {
                Datum = Base::toRadians<double>(Quantity.getValue());
            }
            else {
                Datum = Quantity.getValue();
            }

            int i = 0;
            Index = -1;
            const std::vector<Constraint*>& vals = this->getSketchObjectPtr()->Constraints.getValues();
            for (std::vector<Constraint*>::const_iterator it = vals.begin(); it != vals.end();
                 ++it, ++i) {
                if ((*it)->Name == constrName) {
                    Index = i;
                    break;
                }
            }

            if (Index >= 0) {
                break;
            }
            else {
                std::stringstream str;
                str << "Invalid constraint name: '" << constrName << "'";
                PyErr_SetString(PyExc_ValueError, str.str().c_str());
                return nullptr;
            }
        }

        // handle (string,double)
        PyErr_Clear();
        if (PyArg_ParseTuple(args, "sd", &constrName, &Datum)) {
            Quantity.setValue(Datum);
            int i = 0;
            Index = -1;
            const std::vector<Constraint*>& vals = this->getSketchObjectPtr()->Constraints.getValues();
            for (std::vector<Constraint*>::const_iterator it = vals.begin(); it != vals.end();
                 ++it, ++i) {
                if ((*it)->Name == constrName) {
                    Index = i;
                    break;
                }
            }

            if (Index >= 0) {
                break;
            }
            else {
                std::stringstream str;
                str << "Invalid constraint name: '" << constrName << "'";
                PyErr_SetString(PyExc_ValueError, str.str().c_str());
                return nullptr;
            }
        }

        // error handling
        PyErr_SetString(PyExc_TypeError, "Wrong arguments");
        return nullptr;
    } while (false);

    int err = this->getSketchObjectPtr()->setDatum(Index, Datum);
    if (err) {
        std::stringstream str;
        if (err == -1) {
            str << "Invalid constraint index: " << Index;
        }
        else if (err == -3) {
            str << "Cannot set the datum because the sketch contains conflicting constraints";
        }
        else if (err == -2) {
            str << "Datum " << Quantity.getUserString() << " for the constraint with index "
                << Index << " is invalid";
        }
        else if (err == -4) {
            str << "Negative datum values are not valid for the constraint with index " << Index;
        }
        else if (err == -5) {
            str << "Zero is not a valid datum for the constraint with index " << Index;
        }
        else if (err == -6) {
            str << "Cannot set the datum because of invalid geometry";
        }
        else {
            str << "Unexpected problem at setting datum " << Quantity.getUserString()
                << " for the constraint with index " << Index;
        }
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    Py_Return;
}

PyObject* SketchObjectPy::getDatum(PyObject* args) const
{
    const std::vector<Constraint*>& vals = this->getSketchObjectPtr()->Constraints.getValues();
    Constraint* constr = nullptr;

    do {
        int index = 0;
        if (PyArg_ParseTuple(args, "i", &index)) {
            if (index < 0 || index >= static_cast<int>(vals.size())) {
                PyErr_SetString(PyExc_IndexError, "index out of range");
                return nullptr;
            }

            constr = vals[index];
            break;
        }

        PyErr_Clear();
        char* name;
        if (PyArg_ParseTuple(args, "s", &name)) {
            int id = 0;
            for (std::vector<Constraint*>::const_iterator it = vals.begin(); it != vals.end();
                 ++it, ++id) {
                if (Sketcher::PropertyConstraintList::getConstraintName((*it)->Name, id) == name) {
                    constr = *it;
                    break;
                }
            }

            if (!constr) {
                std::stringstream str;
                str << "Invalid constraint name: '" << name << "'";
                PyErr_SetString(PyExc_NameError, str.str().c_str());
                return nullptr;
            }
            else {
                break;
            }
        }

        // error handling
        PyErr_SetString(PyExc_TypeError, "Wrong arguments");
        return nullptr;
    } while (false);

    ConstraintType type = constr->Type;
    if (type != Distance && type != DistanceX && type != DistanceY && type != Radius
        && type != Diameter && type != Angle) {
        PyErr_SetString(PyExc_TypeError, "Constraint is not a datum");
        return nullptr;
    }

    Base::Quantity datum;
    datum.setValue(constr->getValue());
    if (type == Angle) {
        datum.setValue(Base::toDegrees<double>(datum.getValue()));
        datum.setUnit(Base::Unit::Angle);
    }
    else {
        datum.setUnit(Base::Unit::Length);
    }

    return new Base::QuantityPy(new Base::Quantity(datum));
}

PyObject* SketchObjectPy::setDriving(PyObject* args)
{
    PyObject* driving;
    int constrid;

    if (!PyArg_ParseTuple(args, "iO!", &constrid, &PyBool_Type, &driving)) {
        return nullptr;
    }

    if (this->getSketchObjectPtr()->setDriving(constrid, Base::asBoolean(driving))) {
        std::stringstream str;
        str << "Not able set Driving/reference for constraint with the given index: " << constrid;
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    Py_Return;
}

PyObject* SketchObjectPy::setDatumsDriving(PyObject* args)
{
    PyObject* driving;

    if (!PyArg_ParseTuple(args, "O!", &PyBool_Type, &driving)) {
        return nullptr;
    }

    if (this->getSketchObjectPtr()->setDatumsDriving(Base::asBoolean(driving))) {
        std::stringstream str;
        str << "Not able set all dimensionals driving/reference";
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    Py_Return;
}

PyObject* SketchObjectPy::moveDatumsToEnd(PyObject* args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }

    if (this->getSketchObjectPtr()->moveDatumsToEnd()) {
        std::stringstream str;
        str << "Not able move all dimensionals to end";
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    Py_Return;
}


PyObject* SketchObjectPy::getDriving(PyObject* args) const
{
    int constrid;
    bool driving;

    if (!PyArg_ParseTuple(args, "i", &constrid)) {
        return nullptr;
    }

    if (this->getSketchObjectPtr()->getDriving(constrid, driving)) {
        PyErr_SetString(PyExc_ValueError, "Invalid constraint id");
        return nullptr;
    }

    return Py::new_reference_to(Py::Boolean(driving));
}

PyObject* SketchObjectPy::toggleDriving(PyObject* args)
{
    int constrid;

    if (!PyArg_ParseTuple(args, "i", &constrid)) {
        return nullptr;
    }

    if (this->getSketchObjectPtr()->toggleDriving(constrid)) {
        std::stringstream str;
        str << "Not able toggle Driving for constraint with the given index: " << constrid;
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    Py_Return;
}

PyObject* SketchObjectPy::setVirtualSpace(PyObject* args)
{
    PyObject* invirtualspace;
    PyObject* id_or_ids;

    if (!PyArg_ParseTuple(args, "OO!", &id_or_ids, &PyBool_Type, &invirtualspace)) {
        return nullptr;
    }

    if (PyObject_TypeCheck(id_or_ids, &(PyList_Type))
        || PyObject_TypeCheck(id_or_ids, &(PyTuple_Type))) {
        std::vector<int> constrIds;
        Py::Sequence list(id_or_ids);
        for (Py::Sequence::iterator it = list.begin(); it != list.end(); ++it) {
            if (PyLong_Check((*it).ptr())) {
                constrIds.push_back(PyLong_AsLong((*it).ptr()));
            }
        }

        try {
            int ret = this->getSketchObjectPtr()->setVirtualSpace(
                constrIds,
                Base::asBoolean(invirtualspace)
            );

            if (ret == -1) {
                throw Py::TypeError("Impossible to set virtual space!");
            }
        }
        catch (const Base::ValueError& e) {
            throw Py::ValueError(e.getMessage());
        }

        Py_Return;
    }
    else if (PyLong_Check(id_or_ids)) {
        if (this->getSketchObjectPtr()
                ->setVirtualSpace(PyLong_AsLong(id_or_ids), Base::asBoolean(invirtualspace))) {
            std::stringstream str;
            str << "Not able set virtual space for constraint with the given index: "
                << PyLong_AsLong(id_or_ids);
            PyErr_SetString(PyExc_ValueError, str.str().c_str());
            return nullptr;
        }

        Py_Return;
    }

    std::string error = std::string("type must be list of Constraint Ids, not ");
    error += id_or_ids->ob_type->tp_name;
    throw Py::TypeError(error);
}

PyObject* SketchObjectPy::setVisibility(PyObject* args)
{
    PyObject* isVisible;
    PyObject* id_or_ids;

    if (!PyArg_ParseTuple(args, "OO!", &id_or_ids, &PyBool_Type, &isVisible)) {
        return nullptr;
    }

    if (PyObject_TypeCheck(id_or_ids, &(PyList_Type))
        || PyObject_TypeCheck(id_or_ids, &(PyTuple_Type))) {
        std::vector<int> constrIds;
        Py::Sequence list(id_or_ids);
        for (Py::Sequence::iterator it = list.begin(); it != list.end(); ++it) {
            if (PyLong_Check((*it).ptr())) {
                constrIds.push_back(PyLong_AsLong((*it).ptr()));
            }
        }

        try {
            int ret = this->getSketchObjectPtr()->setVisibility(constrIds, Base::asBoolean(isVisible));

            if (ret == -1) {
                throw Py::TypeError("Impossible to set visibility!");
            }
        }
        catch (const Base::ValueError& e) {
            throw Py::ValueError(e.getMessage());
        }

        Py_Return;
    }
    else if (PyLong_Check(id_or_ids)) {
        if (this->getSketchObjectPtr()
                ->setVisibility(PyLong_AsLong(id_or_ids), Base::asBoolean(isVisible))) {
            std::stringstream str;
            str << "Not able set visibility for constraint with the given index: "
                << PyLong_AsLong(id_or_ids);
            PyErr_SetString(PyExc_ValueError, str.str().c_str());
            return nullptr;
        }

        Py_Return;
    }

    std::string error = std::string("type must be list of Constraint Ids, not ");
    error += id_or_ids->ob_type->tp_name;
    throw Py::TypeError(error);
}

PyObject* SketchObjectPy::getVirtualSpace(PyObject* args)
{
    int constrid;
    bool invirtualspace;

    if (!PyArg_ParseTuple(args, "i", &constrid)) {
        return nullptr;
    }

    if (this->getSketchObjectPtr()->getVirtualSpace(constrid, invirtualspace)) {
        PyErr_SetString(PyExc_ValueError, "Invalid constraint id");
        return nullptr;
    }

    return Py::new_reference_to(Py::Boolean(invirtualspace));
}

PyObject* SketchObjectPy::toggleVirtualSpace(PyObject* args)
{
    int constrid;

    if (!PyArg_ParseTuple(args, "i", &constrid)) {
        return nullptr;
    }

    if (this->getSketchObjectPtr()->toggleVirtualSpace(constrid)) {
        std::stringstream str;
        str << "Not able toggle virtual space for constraint with the given index: " << constrid;
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    Py_Return;
}

PyObject* SketchObjectPy::setActive(PyObject* args)
{
    PyObject* isactive;
    int constrid;

    if (!PyArg_ParseTuple(args, "iO!", &constrid, &PyBool_Type, &isactive)) {
        return nullptr;
    }

    if (this->getSketchObjectPtr()->setActive(constrid, Base::asBoolean(isactive))) {
        std::stringstream str;
        str << "Not able set active/disabled status for constraint with the given index: "
            << constrid;
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    Py_Return;
}

PyObject* SketchObjectPy::getActive(PyObject* args) const
{
    int constrid;
    bool isactive;

    if (!PyArg_ParseTuple(args, "i", &constrid)) {
        return nullptr;
    }

    if (this->getSketchObjectPtr()->getActive(constrid, isactive)) {
        PyErr_SetString(PyExc_ValueError, "Invalid constraint id");
        return nullptr;
    }

    return Py::new_reference_to(Py::Boolean(isactive));
}

PyObject* SketchObjectPy::toggleActive(PyObject* args)
{
    int constrid;

    if (!PyArg_ParseTuple(args, "i", &constrid)) {
        return nullptr;
    }

    if (this->getSketchObjectPtr()->toggleActive(constrid)) {
        std::stringstream str;
        str << "Not able toggle on/off constraint with the given index: " << constrid;
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    Py_Return;
}

PyObject* SketchObjectPy::getLabelPosition(PyObject* args) const
{
    int constrid {};
    float pos {};

    if (!PyArg_ParseTuple(args, "i", &constrid)) {
        return nullptr;
    }

    if (this->getSketchObjectPtr()->getLabelPosition(constrid, pos)) {
        PyErr_SetString(PyExc_ValueError, "Invalid constraint id");
        return nullptr;
    }

    return Py::new_reference_to(Py::Float(pos));
}

PyObject* SketchObjectPy::setLabelPosition(PyObject* args)
{
    int constrid {};
    float pos {};

    if (!PyArg_ParseTuple(args, "if", &constrid, &pos)) {
        return nullptr;
    }

    if (this->getSketchObjectPtr()->setLabelPosition(constrid, pos)) {
        PyErr_SetString(PyExc_ValueError, "Invalid constraint id");
        return nullptr;
    }

    Py_Return;
}

PyObject* SketchObjectPy::getLabelDistance(PyObject* args) const
{
    int constrid {};
    float dist {};

    if (!PyArg_ParseTuple(args, "i", &constrid)) {
        return nullptr;
    }

    if (this->getSketchObjectPtr()->getLabelDistance(constrid, dist)) {
        PyErr_SetString(PyExc_ValueError, "Invalid constraint id");
        return nullptr;
    }

    return Py::new_reference_to(Py::Float(dist));
}

PyObject* SketchObjectPy::setLabelDistance(PyObject* args)
{
    int constrid {};
    float dist {};

    if (!PyArg_ParseTuple(args, "if", &constrid, &dist)) {
        return nullptr;
    }

    if (this->getSketchObjectPtr()->setLabelDistance(constrid, dist)) {
        PyErr_SetString(PyExc_ValueError, "Invalid constraint id");
        return nullptr;
    }

    Py_Return;
}

PyObject* SketchObjectPy::moveGeometries(PyObject* args)
{
    PyObject* pyList;
    PyObject* pcObj;
    int relative = 0;

    // Parse arguments: list of pairs, Base::VectorPy, optional relative flag
    if (!PyArg_ParseTuple(
            args,
            "O!O!|i",
            &PyList_Type,
            &pyList,  // List of pairs (geoId, pointPos)
            &(Base::VectorPy::Type),
            &pcObj,  // Target vector
            &relative
        )) {  // Optional relative flag
        return nullptr;
    }

    // Convert Python list to std::vector<GeoElementId>
    std::vector<GeoElementId> geoEltIds;
    Py_ssize_t listSize = PyList_Size(pyList);

    for (Py_ssize_t i = 0; i < listSize; ++i) {
        PyObject* pyPair = PyList_GetItem(pyList, i);  // Borrowed reference

        if (!PyTuple_Check(pyPair) || PyTuple_Size(pyPair) != 2) {
            PyErr_SetString(PyExc_ValueError, "List must contain pairs (geoId, pointPos).");
            return nullptr;
        }

        int geoId = PyLong_AsLong(PyTuple_GetItem(pyPair, 0));
        int pointPos = PyLong_AsLong(PyTuple_GetItem(pyPair, 1));

        if (PyErr_Occurred()) {
            PyErr_SetString(PyExc_ValueError, "Invalid geoId or pointPos in the list.");
            return nullptr;
        }

        geoEltIds.emplace_back(GeoElementId(geoId, static_cast<Sketcher::PointPos>(pointPos)));
    }

    // Convert Python vector to Base::Vector3d
    Base::Vector3d v1 = static_cast<Base::VectorPy*>(pcObj)->value();

    // Call the C++ method
    if (this->getSketchObjectPtr()->moveGeometries(geoEltIds, v1, (relative > 0))) {
        PyErr_SetString(PyExc_ValueError, "Failed to move geometries.");
        return nullptr;
    }

    Py_RETURN_NONE;
}

PyObject* SketchObjectPy::moveGeometry(PyObject* args)
{
    PyObject* pcObj;
    int GeoId, PointType;
    int relative = 0;

    if (
        !PyArg_ParseTuple(args, "iiO!|i", &GeoId, &PointType, &(Base::VectorPy::Type), &pcObj, &relative)
    ) {
        return nullptr;
    }

    Base::Vector3d v1 = static_cast<Base::VectorPy*>(pcObj)->value();

    if (this->getSketchObjectPtr()
            ->moveGeometry(GeoId, static_cast<Sketcher::PointPos>(PointType), v1, (relative > 0))) {
        std::stringstream str;
        str << "Not able to move point with the id and type: (" << GeoId << ", " << PointType << ")";
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    Py_Return;
}

PyObject* SketchObjectPy::getGeoVertexIndex(PyObject* args) const
{
    int index;
    if (!PyArg_ParseTuple(args, "i", &index)) {
        return nullptr;
    }

    SketchObject* obj = this->getSketchObjectPtr();
    int geoId;
    PointPos posId;
    obj->getGeoVertexIndex(index, geoId, posId);
    Py::Tuple tuple(2);
    tuple.setItem(0, Py::Long(geoId));
    tuple.setItem(1, Py::Long(static_cast<int>(posId)));
    return Py::new_reference_to(tuple);
}

PyObject* SketchObjectPy::getPoint(PyObject* args) const
{
    int GeoId, PointType;
    if (!PyArg_ParseTuple(args, "ii", &GeoId, &PointType)) {
        return nullptr;
    }

    if (PointType < 0 || PointType > 3) {
        PyErr_SetString(PyExc_ValueError, "Invalid point type");
        return nullptr;
    }

    SketchObject* obj = this->getSketchObjectPtr();
    if (GeoId > obj->getHighestCurveIndex() || -GeoId > obj->getExternalGeometryCount()) {
        PyErr_SetString(PyExc_ValueError, "Invalid geometry Id");
        return nullptr;
    }

    return new Base::VectorPy(
        new Base::Vector3d(obj->getPoint(GeoId, static_cast<Sketcher::PointPos>(PointType)))
    );
}

PyObject* SketchObjectPy::getAxis(PyObject* args) const
{
    int AxId;
    if (!PyArg_ParseTuple(args, "i", &AxId)) {
        return nullptr;
    }

    return new Base::AxisPy(new Base::Axis(this->getSketchObjectPtr()->getAxis(AxId)));
}

PyObject* SketchObjectPy::getProfileDiagnostics(PyObject* args) const
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }

    const auto* sketch = getSketchObjectPtr();
    const auto& sketchShape = sketch->Shape.getShape();
    const auto& internalShape = sketch->InternalShape.getShape();
    const auto wires = sketchShape.getSubTopoShapes(TopAbs_WIRE);
    const auto faces = internalShape.getSubTopoShapes(TopAbs_FACE);

    PyObject* result = PyDict_New();
    PyObject* wireItems = PyList_New(static_cast<Py_ssize_t>(wires.size()));
    int closedWireCount = 0;
    int faceBuildableWireCount = 0;
    for (std::size_t index = 0; index < wires.size(); ++index) {
        const auto& wire = wires[index];
        const bool closed = wire.isClosed();
        if (closed) {
            ++closedWireCount;
        }
        const TopoDS_Shape& rawWire = wire.getShape();
        BRepCheck_Analyzer analyzer(rawWire);
        const bool valid = analyzer.IsValid();
        PyObject* item = PyDict_New();
        PyObject* wireIndex = PyLong_FromSize_t(index);
        PyObject* closedValue = PyBool_FromLong(closed);
        PyObject* validValue = PyBool_FromLong(valid);
        PyObject* edgeCount = PyLong_FromUnsignedLong(wire.countSubShapes(TopAbs_EDGE));
        PyDict_SetItemString(item, "wire_index", wireIndex);
        PyDict_SetItemString(item, "closed", closedValue);
        PyDict_SetItemString(item, "brep_valid", validValue);
        PyDict_SetItemString(item, "edge_count", edgeCount);
        Py_DECREF(wireIndex);
        Py_DECREF(closedValue);
        Py_DECREF(validValue);
        Py_DECREF(edgeCount);

        PyObject* statuses = PyList_New(0);
        const Handle(BRepCheck_Result)& checkResult = analyzer.Result(rawWire);
        if (!checkResult.IsNull()) {
            BRepCheck_ListIteratorOfListOfStatus statusIt;
            statusIt.Initialize(checkResult->Status());
            for (; statusIt.More(); statusIt.Next()) {
                const auto status = statusIt.Value();
                if (status == BRepCheck_NoError) {
                    continue;
                }
                PyObject* statusValue = PyLong_FromLong(static_cast<long>(status));
                PyList_Append(statuses, statusValue);
                Py_DECREF(statusValue);
            }
        }
        PyDict_SetItemString(item, "brep_status_codes", statuses);
        Py_DECREF(statuses);

        if (!closed) {
            TopoDS_Vertex first;
            TopoDS_Vertex last;
            TopExp::Vertices(TopoDS::Wire(rawWire), first, last);
            if (!first.IsNull() && !last.IsNull()) {
                const gp_Pnt firstPoint = BRep_Tool::Pnt(first);
                const gp_Pnt lastPoint = BRep_Tool::Pnt(last);
                PyObject* start = Py_BuildValue(
                    "[ddd]", firstPoint.X(), firstPoint.Y(), firstPoint.Z());
                PyObject* end = Py_BuildValue(
                    "[ddd]", lastPoint.X(), lastPoint.Y(), lastPoint.Z());
                PyObject* gap = PyFloat_FromDouble(firstPoint.Distance(lastPoint));
                PyDict_SetItemString(item, "open_start", start);
                PyDict_SetItemString(item, "open_end", end);
                PyDict_SetItemString(item, "closure_gap", gap);
                Py_DECREF(start);
                Py_DECREF(end);
                Py_DECREF(gap);
            }
        }
        if (closed && valid) {
            ++faceBuildableWireCount;
        }
        PyList_SET_ITEM(wireItems, static_cast<Py_ssize_t>(index), item);
    }

    PyObject* faceItems = PyList_New(static_cast<Py_ssize_t>(faces.size()));
    for (std::size_t index = 0; index < faces.size(); ++index) {
        const auto& face = faces[index];
        PyObject* item = PyDict_New();
        PyObject* faceIndex = PyLong_FromSize_t(index);
        PyObject* valid = PyBool_FromLong(face.isValid());
        PyObject* wireCount = PyLong_FromUnsignedLong(face.countSubShapes(TopAbs_WIRE));
        PyObject* orientation = PyLong_FromLong(
            static_cast<long>(face.getShape().Orientation()));
        PyDict_SetItemString(item, "face_index", faceIndex);
        PyDict_SetItemString(item, "brep_valid", valid);
        PyDict_SetItemString(item, "wire_count", wireCount);
        PyDict_SetItemString(item, "orientation", orientation);
        Py_DECREF(faceIndex);
        Py_DECREF(valid);
        Py_DECREF(wireCount);
        Py_DECREF(orientation);
        PyList_SET_ITEM(faceItems, static_cast<Py_ssize_t>(index), item);
    }

    const bool faceMakerSucceeded = !faces.empty();
    const char* faceMakerStatus = faceMakerSucceeded
        ? "succeeded"
        : (closedWireCount > 0 ? "failed" : "not_applicable");
    PyObject* geometryCount = PyLong_FromLong(sketch->Geometry.getSize());
    PyObject* wireCount = PyLong_FromSize_t(wires.size());
    PyObject* closedCount = PyLong_FromLong(closedWireCount);
    PyObject* faceCount = PyLong_FromSize_t(faces.size());
    PyObject* faceBuildableCount = PyLong_FromLong(faceBuildableWireCount);
    PyObject* makerStatus = PyUnicode_FromString(faceMakerStatus);
    PyObject* makerSucceeded = PyBool_FromLong(faceMakerSucceeded);
    PyObject* supportPlane = PyUnicode_FromString("sketch_xy");
    PyDict_SetItemString(result, "geometry_count", geometryCount);
    PyDict_SetItemString(result, "wire_count", wireCount);
    PyDict_SetItemString(result, "closed_wire_count", closedCount);
    PyDict_SetItemString(result, "face_count", faceCount);
    PyDict_SetItemString(result, "face_buildable_wire_count", faceBuildableCount);
    PyDict_SetItemString(result, "wires", wireItems);
    PyDict_SetItemString(result, "faces", faceItems);
    PyDict_SetItemString(result, "face_maker_status", makerStatus);
    PyDict_SetItemString(result, "face_maker_succeeded", makerSucceeded);
    PyDict_SetItemString(result, "support_plane", supportPlane);
    Py_DECREF(geometryCount);
    Py_DECREF(wireCount);
    Py_DECREF(closedCount);
    Py_DECREF(faceCount);
    Py_DECREF(faceBuildableCount);
    Py_DECREF(wireItems);
    Py_DECREF(faceItems);
    Py_DECREF(makerStatus);
    Py_DECREF(makerSucceeded);
    Py_DECREF(supportPlane);
    return result;
}

PyObject* SketchObjectPy::fillet(PyObject* args)
{
    PyObject *pcObj1, *pcObj2;
    int geoId1, geoId2, posId1;
    int trim = true;
    PyObject* createCorner = Py_False;
    PyObject* chamfer = Py_False;
    double radius;

    // Two Lines, radius
    if (PyArg_ParseTuple(
            args,
            "iiO!O!d|iO!O!",
            &geoId1,
            &geoId2,
            &(Base::VectorPy::Type),
            &pcObj1,
            &(Base::VectorPy::Type),
            &pcObj2,
            &radius,
            &trim,
            &PyBool_Type,
            &createCorner,
            &PyBool_Type,
            &chamfer
        )) {
        // The i for &trim should probably have been a bool like &createCorner, but we'll leave it
        // an int for backward compatibility (and because python will accept a bool there anyway)

        Base::Vector3d v1 = static_cast<Base::VectorPy*>(pcObj1)->value();
        Base::Vector3d v2 = static_cast<Base::VectorPy*>(pcObj2)->value();

        auto* sketch = this->getSketchObjectPtr();
        const auto before = mutationSnapshot(sketch);
        if (sketch->fillet(
                geoId1,
                geoId2,
                v1,
                v2,
                radius,
                trim,
                Base::asBoolean(createCorner),
                Base::asBoolean(chamfer)
            )) {
            std::stringstream str;
            str << "Not able to fillet curves with ids : (" << geoId1 << ", " << geoId2
                << ") and points (" << v1.x << ", " << v1.y << ", " << v1.z << ") & "
                << "(" << v2.x << ", " << v2.y << ", " << v2.z << ")";
            THROWM(Base::ValueError, str.str().c_str())
            return nullptr;
        }
        return mutationResult(before, sketch);
    }

    PyErr_Clear();
    // Point, radius
    if (PyArg_ParseTuple(
            args,
            "iid|iO!O!",
            &geoId1,
            &posId1,
            &radius,
            &trim,
            &PyBool_Type,
            &createCorner,
            &PyBool_Type,
            &chamfer
        )) {
        auto* sketch = this->getSketchObjectPtr();
        const auto before = mutationSnapshot(sketch);
        if (sketch->fillet(
                geoId1,
                static_cast<Sketcher::PointPos>(posId1),
                radius,
                trim,
                Base::asBoolean(createCorner),
                Base::asBoolean(chamfer)
            )) {
            std::stringstream str;
            str << "Not able to fillet point with ( geoId: " << geoId1 << ", PointPos: " << posId1
                << " )";
            PyErr_SetString(PyExc_ValueError, str.str().c_str());
            return nullptr;
        }
        return mutationResult(before, sketch);
    }

    PyErr_SetString(
        PyExc_TypeError,
        "fillet() method accepts:\n"
        "-- int,int,Vector,Vector,float,[bool],[bool]\n"
        "-- int,int,float,[bool],[bool]\n"
    );
    return nullptr;
}

PyObject* SketchObjectPy::trim(PyObject* args)
{
    PyObject* pcObj;
    int GeoId;

    if (!PyArg_ParseTuple(args, "iO!", &GeoId, &(Base::VectorPy::Type), &pcObj)) {
        return nullptr;
    }

    Base::Vector3d v1 = static_cast<Base::VectorPy*>(pcObj)->value();

    auto* sketch = this->getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    if (sketch->trim(GeoId, v1)) {
        std::stringstream str;
        str << "Not able to trim curve with the given index: " << GeoId;
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    return mutationResult(before, sketch);
}

PyObject* SketchObjectPy::extend(PyObject* args)
{
    double increment;
    int endPoint;
    int GeoId;

    if (PyArg_ParseTuple(args, "idi", &GeoId, &increment, &endPoint)) {
        auto* sketch = this->getSketchObjectPtr();
        const auto before = mutationSnapshot(sketch);
        if (sketch->extend(GeoId, increment, static_cast<Sketcher::PointPos>(endPoint))) {
            std::stringstream str;
            str << "Not able to extend geometry with id : (" << GeoId << ") for increment ("
                << increment << ") and point position (" << endPoint << ")";
            PyErr_SetString(PyExc_ValueError, str.str().c_str());
            return nullptr;
        }
        return mutationResult(before, sketch);
    }

    PyErr_SetString(
        PyExc_TypeError,
        "extend() method accepts:\n"
        "-- int,float,int\n"
    );
    return nullptr;
}

PyObject* SketchObjectPy::split(PyObject* args)
{
    PyObject* pcObj;
    int GeoId;

    if (!PyArg_ParseTuple(args, "iO!", &GeoId, &(Base::VectorPy::Type), &pcObj)) {
        return nullptr;
    }

    Base::Vector3d v1 = static_cast<Base::VectorPy*>(pcObj)->value();
    auto* sketch = this->getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    try {
        if (sketch->split(GeoId, v1)) {
            std::stringstream str;
            str << "Not able to split curve with the given index: " << GeoId;
            PyErr_SetString(PyExc_ValueError, str.str().c_str());
            return nullptr;
        }
    }
    catch (const Base::ValueError& e) {
        throw Py::ValueError(e.getMessage());
    }

    return mutationResult(before, sketch);
}

PyObject* SketchObjectPy::diagnoseJoinCurves(PyObject* args)
{
    PyObject* firstGeometryObject = nullptr;
    PyObject* firstEndpointObject = nullptr;
    PyObject* secondGeometryObject = nullptr;
    PyObject* secondEndpointObject = nullptr;
    if (!PyArg_ParseTuple(args,
                          "OOOO",
                          &firstGeometryObject,
                          &firstEndpointObject,
                          &secondGeometryObject,
                          &secondEndpointObject)) {
        return nullptr;
    }
    int firstGeometry = -1;
    int secondGeometry = -1;
    PointPos firstEndpoint = PointPos::none;
    PointPos secondEndpoint = PointPos::none;
    if (!joinCurvesArgumentsFromPython(firstGeometryObject,
                                       firstEndpointObject,
                                       secondGeometryObject,
                                       secondEndpointObject,
                                       firstGeometry,
                                       firstEndpoint,
                                       secondGeometry,
                                       secondEndpoint)) {
        return nullptr;
    }
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    const int continuity = joinEndpointContinuity(sketch,
                                                  firstGeometry,
                                                  firstEndpoint,
                                                  secondGeometry,
                                                  secondEndpoint);
    auto diagnostic = sketch->diagnoseJoinCurves(firstGeometry,
                                                 firstEndpoint,
                                                 secondGeometry,
                                                 secondEndpoint);
    if (!diagnostic) {
        PyErr_SetString(PyExc_ValueError,
                        "The exact human Join Curves target is unavailable.");
        return nullptr;
    }
    return joinCurvesDiagnosticResult(std::move(diagnostic),
                                      before,
                                      firstGeometry,
                                      firstEndpoint,
                                      secondGeometry,
                                      secondEndpoint,
                                      continuity);
}

PyObject* SketchObjectPy::joinCurvesExact(PyObject* args)
{
    PyObject* firstGeometryObject = nullptr;
    PyObject* firstEndpointObject = nullptr;
    PyObject* secondGeometryObject = nullptr;
    PyObject* secondEndpointObject = nullptr;
    if (!PyArg_ParseTuple(args,
                          "OOOO",
                          &firstGeometryObject,
                          &firstEndpointObject,
                          &secondGeometryObject,
                          &secondEndpointObject)) {
        return nullptr;
    }
    int firstGeometry = -1;
    int secondGeometry = -1;
    PointPos firstEndpoint = PointPos::none;
    PointPos secondEndpoint = PointPos::none;
    if (!joinCurvesArgumentsFromPython(firstGeometryObject,
                                       firstEndpointObject,
                                       secondGeometryObject,
                                       secondEndpointObject,
                                       firstGeometry,
                                       firstEndpoint,
                                       secondGeometry,
                                       secondEndpoint)) {
        return nullptr;
    }
    auto* sketch = getSketchObjectPtr();
    const auto before = mutationSnapshot(sketch);
    if (sketch->joinCurvesExact(firstGeometry,
                                firstEndpoint,
                                secondGeometry,
                                secondEndpoint)
        <= 0) {
        PyErr_SetString(PyExc_ValueError,
                        "Sketcher rejected the exact Join Curves operation.");
        return nullptr;
    }
    return mutationResult(before, sketch);
}

PyObject* SketchObjectPy::join(PyObject* args)
{
    int GeoId1(Sketcher::GeoEnum::GeoUndef), GeoId2(Sketcher::GeoEnum::GeoUndef);
    int PosId1 = static_cast<int>(Sketcher::PointPos::none),
        PosId2 = static_cast<int>(Sketcher::PointPos::none);
    int continuity = 0;

    if (!PyArg_ParseTuple(args, "iiii|i", &GeoId1, &PosId1, &GeoId2, &PosId2, &continuity)) {
        return nullptr;
    }

    if (this->getSketchObjectPtr()
            ->join(GeoId1, (Sketcher::PointPos)PosId1, GeoId2, (Sketcher::PointPos)PosId2, continuity)) {
        std::stringstream str;
        str << "Not able to join the curves with end points: (" << GeoId1 << ", " << PosId1
            << "), (" << GeoId2 << ", " << PosId2 << ")";
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    Py_Return;
}

PyObject* SketchObjectPy::addSymmetric(PyObject* args)
{
    PyObject* pcObj;
    int refGeoId;
    int refPosId = static_cast<int>(Sketcher::PointPos::none);

    if (!PyArg_ParseTuple(args, "Oi|i", &pcObj, &refGeoId, &refPosId)) {
        return nullptr;
    }

    if (PyObject_TypeCheck(pcObj, &(PyList_Type)) || PyObject_TypeCheck(pcObj, &(PyTuple_Type))) {
        std::vector<int> geoIdList;
        Py::Sequence list(pcObj);
        for (Py::Sequence::iterator it = list.begin(); it != list.end(); ++it) {
            if (PyLong_Check((*it).ptr())) {
                geoIdList.push_back(PyLong_AsLong((*it).ptr()));
            }
        }

        int ret = this->getSketchObjectPtr()
                      ->addSymmetric(geoIdList, refGeoId, static_cast<Sketcher::PointPos>(refPosId))
            + 1;

        if (ret == -1) {
            throw Py::TypeError("Symmetric operation unsuccessful!");
        }

        std::size_t numGeo = geoIdList.size();
        Py::Tuple tuple(numGeo);
        for (std::size_t i = 0; i < numGeo; ++i) {
            int geoId = ret - int(numGeo - i);
            tuple.setItem(i, Py::Long(geoId));
        }

        return Py::new_reference_to(tuple);
    }

    std::string error = std::string("type must be list of GeoIds, not ");
    error += pcObj->ob_type->tp_name;
    throw Py::TypeError(error);
}

PyObject* SketchObjectPy::addCopy(PyObject* args)
{
    PyObject *pcObj, *pcVect;
    PyObject* clone = Py_False;

    if (
        !PyArg_ParseTuple(args, "OO!|O!", &pcObj, &(Base::VectorPy::Type), &pcVect, &PyBool_Type, &clone)
    ) {
        return nullptr;
    }

    Base::Vector3d vect = static_cast<Base::VectorPy*>(pcVect)->value();

    if (PyObject_TypeCheck(pcObj, &(PyList_Type)) || PyObject_TypeCheck(pcObj, &(PyTuple_Type))) {
        std::vector<int> geoIdList;
        Py::Sequence list(pcObj);
        for (Py::Sequence::iterator it = list.begin(); it != list.end(); ++it) {
            if (PyLong_Check((*it).ptr())) {
                geoIdList.push_back(PyLong_AsLong((*it).ptr()));
            }
        }

        try {
            int ret = this->getSketchObjectPtr()->addCopy(geoIdList, vect, false, Base::asBoolean(clone))
                + 1;

            if (ret == -1) {
                throw Py::TypeError("Copy operation unsuccessful!");
            }

            std::size_t numGeo = geoIdList.size();
            Py::Tuple tuple(numGeo);
            for (std::size_t i = 0; i < numGeo; ++i) {
                int geoId = ret - int(numGeo - i);
                tuple.setItem(i, Py::Long(geoId));
            }

            return Py::new_reference_to(tuple);
        }
        catch (const Base::ValueError& e) {
            throw Py::ValueError(e.getMessage());
        }
    }

    std::string error = std::string("type must be list of GeoIds, not ");
    error += pcObj->ob_type->tp_name;
    throw Py::TypeError(error);
}

PyObject* SketchObjectPy::addMove(PyObject* args)
{
    PyObject *pcObj, *pcVect;

    if (!PyArg_ParseTuple(args, "OO!", &pcObj, &(Base::VectorPy::Type), &pcVect)) {
        return nullptr;
    }

    Base::Vector3d vect = static_cast<Base::VectorPy*>(pcVect)->value();

    if (PyObject_TypeCheck(pcObj, &(PyList_Type)) || PyObject_TypeCheck(pcObj, &(PyTuple_Type))) {
        std::vector<int> geoIdList;
        Py::Sequence list(pcObj);
        for (Py::Sequence::iterator it = list.begin(); it != list.end(); ++it) {
            if (PyLong_Check((*it).ptr())) {
                geoIdList.push_back(PyLong_AsLong((*it).ptr()));
            }
        }

        this->getSketchObjectPtr()->addCopy(geoIdList, vect, true);

        Py_Return;
    }

    std::string error = std::string("type must be list of GeoIds, not ");
    error += pcObj->ob_type->tp_name;
    throw Py::TypeError(error);
}

PyObject* SketchObjectPy::addRectangularArray(PyObject* args)
{
    PyObject *pcObj, *pcVect;
    int rows, cols;
    double perpscale = 1.0;
    PyObject* constraindisplacement = Py_False;
    PyObject* clone = Py_False;

    if (!PyArg_ParseTuple(
            args,
            "OO!O!ii|O!d",
            &pcObj,
            &(Base::VectorPy::Type),
            &pcVect,
            &PyBool_Type,
            &clone,
            &rows,
            &cols,
            &PyBool_Type,
            &constraindisplacement,
            &perpscale
        )) {
        return nullptr;
    }

    Base::Vector3d vect = static_cast<Base::VectorPy*>(pcVect)->value();

    if (PyObject_TypeCheck(pcObj, &(PyList_Type)) || PyObject_TypeCheck(pcObj, &(PyTuple_Type))) {
        std::vector<int> geoIdList;
        Py::Sequence list(pcObj);
        for (Py::Sequence::iterator it = list.begin(); it != list.end(); ++it) {
            if (PyLong_Check((*it).ptr())) {
                geoIdList.push_back(PyLong_AsLong((*it).ptr()));
            }
        }

        try {
            int ret = this->getSketchObjectPtr()->addCopy(
                          geoIdList,
                          vect,
                          false,
                          Base::asBoolean(clone),
                          rows,
                          cols,
                          Base::asBoolean(constraindisplacement),
                          perpscale
                      )
                + 1;

            if (ret == -1) {
                throw Py::TypeError("Copy operation unsuccessful!");
            }
        }
        catch (const Base::ValueError& e) {
            throw Py::ValueError(e.getMessage());
        }

        Py_Return;
    }

    std::string error = std::string("type must be list of GeoIds, not ");
    error += pcObj->ob_type->tp_name;
    throw Py::TypeError(error);
}

PyObject* SketchObjectPy::removeAxesAlignment(PyObject* args)
{
    PyObject* pcObj;

    if (!PyArg_ParseTuple(args, "O", &pcObj)) {
        return nullptr;
    }

    if (PyObject_TypeCheck(pcObj, &(PyList_Type)) || PyObject_TypeCheck(pcObj, &(PyTuple_Type))) {
        std::vector<int> geoIdList;
        Py::Sequence list(pcObj);
        for (Py::Sequence::iterator it = list.begin(); it != list.end(); ++it) {
            if (PyLong_Check((*it).ptr())) {
                geoIdList.push_back(PyLong_AsLong((*it).ptr()));
            }
        }

        int ret = this->getSketchObjectPtr()->removeAxesAlignment(geoIdList) + 1;

        if (ret == -1) {
            throw Py::TypeError("Operation unsuccessful!");
        }

        Py_Return;
    }

    std::string error = std::string("type must be list of GeoIds, not ");
    error += pcObj->ob_type->tp_name;
    throw Py::TypeError(error);
}

PyObject* SketchObjectPy::calculateAngleViaPoint(PyObject* args)
{
    int GeoId1 = 0, GeoId2 = 0;
    double px = 0, py = 0;
    if (!PyArg_ParseTuple(args, "iidd", &GeoId1, &GeoId2, &px, &py)) {
        return nullptr;
    }

    SketchObject* obj = this->getSketchObjectPtr();
    if (GeoId1 > obj->getHighestCurveIndex() || -GeoId1 > obj->getExternalGeometryCount()
        || GeoId2 > obj->getHighestCurveIndex() || -GeoId2 > obj->getExternalGeometryCount()) {
        PyErr_SetString(PyExc_ValueError, "Invalid geometry Id");
        return nullptr;
    }
    double ang = obj->calculateAngleViaPoint(GeoId1, GeoId2, px, py);

    return Py::new_reference_to(Py::Float(ang));
}

PyObject* SketchObjectPy::isPointOnCurve(PyObject* args)
{
    int GeoId = GeoEnum::GeoUndef;
    double px = 0, py = 0;
    if (!PyArg_ParseTuple(args, "idd", &GeoId, &px, &py)) {
        return nullptr;
    }

    SketchObject* obj = this->getSketchObjectPtr();
    if (GeoId > obj->getHighestCurveIndex() || -GeoId > obj->getExternalGeometryCount()) {
        PyErr_SetString(PyExc_ValueError, "Invalid geometry Id");
        return nullptr;
    }

    return Py::new_reference_to(Py::Boolean(obj->isPointOnCurve(GeoId, px, py)));
}

PyObject* SketchObjectPy::calculateConstraintError(PyObject* args)
{
    int ic = -1;
    if (!PyArg_ParseTuple(args, "i", &ic)) {
        return nullptr;
    }

    SketchObject* obj = this->getSketchObjectPtr();
    if (ic >= obj->Constraints.getSize() || ic < 0) {
        PyErr_SetString(PyExc_ValueError, "Invalid constraint Id");
        return nullptr;
    }
    double err = obj->calculateConstraintError(ic);

    return Py::new_reference_to(Py::Float(err));
}

PyObject* SketchObjectPy::changeConstraintsLocking(PyObject* args)
{
    int bLock = 0;
    if (!PyArg_ParseTuple(args, "i", &bLock)) {
        return nullptr;
    }

    SketchObject* obj = this->getSketchObjectPtr();

    int naff = obj->changeConstraintsLocking((bool)bLock);

    return Py::new_reference_to(Py::Long(naff));
}

// Deprecated
PyObject* SketchObjectPy::ExposeInternalGeometry(PyObject* args)
{
    int GeoId;

    if (!PyArg_ParseTuple(args, "i", &GeoId)) {
        return nullptr;
    }

    if (this->getSketchObjectPtr()->exposeInternalGeometry(GeoId) == -1) {
        std::stringstream str;
        str << "Object does not support internal geometry: " << GeoId;
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    Py_Return;
}

// Deprecated
PyObject* SketchObjectPy::DeleteUnusedInternalGeometry(PyObject* args)
{
    int GeoId;

    if (!PyArg_ParseTuple(args, "i", &GeoId)) {
        return nullptr;
    }

    if (this->getSketchObjectPtr()->deleteUnusedInternalGeometry(GeoId) == -1) {
        std::stringstream str;
        str << "Object does not support internal geometry: " << GeoId;
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    Py_Return;
}

PyObject* SketchObjectPy::exposeInternalGeometry(PyObject* args)
{
    int GeoId;

    if (!PyArg_ParseTuple(args, "i", &GeoId)) {
        return nullptr;
    }

    auto* sketch = this->getSketchObjectPtr();
    const int beforeCount = sketch->Geometry.getSize();
    const int added = sketch->exposeInternalGeometry(GeoId);
    if (added == -1) {
        std::stringstream str;
        str << "Object does not support internal geometry: " << GeoId;
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    const int afterCount = sketch->Geometry.getSize();
    PyObject* result = PyDict_New();
    PyObject* source = PyLong_FromLong(GeoId);
    PyObject* before = PyLong_FromLong(beforeCount);
    PyObject* after = PyLong_FromLong(afterCount);
    PyObject* addedCount = PyLong_FromLong(added);
    PyDict_SetItemString(result, "source_geometry_index", source);
    PyDict_SetItemString(result, "geometry_count_before", before);
    PyDict_SetItemString(result, "geometry_count_after", after);
    PyDict_SetItemString(result, "created_count", addedCount);
    Py_DECREF(source);
    Py_DECREF(before);
    Py_DECREF(after);
    Py_DECREF(addedCount);

    PyObject* created = PyList_New(afterCount - beforeCount);
    for (int index = beforeCount; index < afterCount; ++index) {
        auto facade = sketch->getGeometryFacade(index);
        PyObject* item = PyDict_New();
        PyObject* geometryIndex = PyLong_FromLong(index);
        PyObject* geometryId = PyLong_FromLong(facade ? facade->getId() : -1);
        const auto internalType = facade ? facade->getInternalType() : InternalType::None;
        const char* role = internalType >= InternalType::None
                && internalType < InternalType::NumInternalGeometryType
            ? SketchGeometryExtension::internaltype2str[internalType]
            : "Unknown";
        PyObject* internalRole = PyUnicode_FromString(role);
        PyDict_SetItemString(item, "geometry_index", geometryIndex);
        PyDict_SetItemString(item, "geometry_id", geometryId);
        PyDict_SetItemString(item, "role", internalRole);
        Py_DECREF(geometryIndex);
        Py_DECREF(geometryId);
        Py_DECREF(internalRole);
        PyList_SET_ITEM(created, index - beforeCount, item);
    }
    PyDict_SetItemString(result, "created", created);
    Py_DECREF(created);
    return result;
}

PyObject* SketchObjectPy::deleteUnusedInternalGeometry(PyObject* args)
{
    int GeoId;

    if (!PyArg_ParseTuple(args, "i", &GeoId)) {
        return nullptr;
    }

    if (this->getSketchObjectPtr()->deleteUnusedInternalGeometry(GeoId) == -1) {
        std::stringstream str;
        str << "Object does not support internal geometry: " << GeoId;
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    Py_Return;
}

PyObject* SketchObjectPy::convertToNURBS(PyObject* args)
{
    int GeoId;

    if (!PyArg_ParseTuple(args, "i", &GeoId)) {
        return nullptr;
    }

    if (!this->getSketchObjectPtr()->convertToNURBS(GeoId)) {
        std::stringstream str;
        str << "Object does not support NURBS conversion: " << GeoId;
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    Py_Return;
}

PyObject* SketchObjectPy::increaseBSplineDegree(PyObject* args)
{
    int GeoId;
    int incr = 1;

    if (!PyArg_ParseTuple(args, "i|i", &GeoId, &incr)) {
        return nullptr;
    }

    if (!this->getSketchObjectPtr()->increaseBSplineDegree(GeoId, incr)) {
        std::stringstream str;
        str << "Degree increase failed for: " << GeoId;
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    Py_Return;
}

PyObject* SketchObjectPy::decreaseBSplineDegree(PyObject* args)
{
    int GeoId;
    int decr = 1;

    if (!PyArg_ParseTuple(args, "i|i", &GeoId, &decr)) {
        return nullptr;
    }

    bool ok = this->getSketchObjectPtr()->decreaseBSplineDegree(GeoId, decr);
    return Py_BuildValue("O", (ok ? Py_True : Py_False));
}

PyObject* SketchObjectPy::modifyBSplineKnotMultiplicity(PyObject* args)
{
    int GeoId;
    int knotIndex;
    int multiplicity = 1;

    if (!PyArg_ParseTuple(args, "ii|i", &GeoId, &knotIndex, &multiplicity)) {
        return nullptr;
    }

    if (!this->getSketchObjectPtr()->modifyBSplineKnotMultiplicity(GeoId, knotIndex, multiplicity)) {
        std::stringstream str;
        str << "Multiplicity modification failed for: " << GeoId;
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    Py_Return;
}

PyObject* SketchObjectPy::insertBSplineKnot(PyObject* args)
{
    int GeoId;
    double knotParam;
    int multiplicity = 1;

    if (!PyArg_ParseTuple(args, "id|i", &GeoId, &knotParam, &multiplicity)) {
        return nullptr;
    }

    if (!this->getSketchObjectPtr()->insertBSplineKnot(GeoId, knotParam, multiplicity)) {
        std::stringstream str;
        str << "Knot insertion failed for: " << GeoId;
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    Py_Return;
}

PyObject* SketchObjectPy::autoconstraint(PyObject* args)
{
    double precision = Precision::Confusion() * 1000;
    double angleprecision = std::numbers::pi / 8;
    PyObject* includeconstruction = Py_True;


    if (
        !PyArg_ParseTuple(args, "|ddO!", &precision, &angleprecision, &PyBool_Type, &includeconstruction)
    ) {
        return nullptr;
    }

    if (this->getSketchObjectPtr()
            ->autoConstraint(precision, angleprecision, Base::asBoolean(includeconstruction))) {
        std::stringstream str;
        str << "Unable to autoconstraint";
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    Py_Return;
}

PyObject* SketchObjectPy::detectMissingPointOnPointConstraints(PyObject* args)
{
    double precision = Precision::Confusion() * 1000;
    PyObject* includeconstruction = Py_True;

    if (!PyArg_ParseTuple(args, "|dO!", &precision, &PyBool_Type, &includeconstruction)) {
        return nullptr;
    }

    return Py::new_reference_to(
        Py::Long(this->getSketchObjectPtr()->detectMissingPointOnPointConstraints(
            precision,
            Base::asBoolean(includeconstruction)
        ))
    );
}

PyObject* SketchObjectPy::detectMissingVerticalHorizontalConstraints(PyObject* args)
{
    double angleprecision = std::numbers::pi / 8;

    if (!PyArg_ParseTuple(args, "|d", &angleprecision)) {
        return nullptr;
    }

    return Py::new_reference_to(
        Py::Long(this->getSketchObjectPtr()->detectMissingVerticalHorizontalConstraints(angleprecision))
    );
}

PyObject* SketchObjectPy::detectMissingEqualityConstraints(PyObject* args)
{
    double precision = Precision::Confusion() * 1000;

    if (!PyArg_ParseTuple(args, "|d", &precision)) {
        return nullptr;
    }

    return Py::new_reference_to(
        Py::Long(this->getSketchObjectPtr()->detectMissingEqualityConstraints(precision))
    );
}

PyObject* SketchObjectPy::analyseMissingPointOnPointCoincident(PyObject* args)
{
    double angleprecision = std::numbers::pi / 8;

    if (!PyArg_ParseTuple(args, "|d", &angleprecision)) {
        return nullptr;
    }

    this->getSketchObjectPtr()->analyseMissingPointOnPointCoincident(angleprecision);

    Py_Return;
}

PyObject* SketchObjectPy::makeMissingPointOnPointCoincident(PyObject* args)
{

    PyObject* onebyone = Py_False;

    if (!PyArg_ParseTuple(args, "|O!", &PyBool_Type, &onebyone)) {
        return nullptr;
    }

    this->getSketchObjectPtr()->makeMissingPointOnPointCoincident(Base::asBoolean(onebyone));

    Py_Return;
}

PyObject* SketchObjectPy::makeMissingVerticalHorizontal(PyObject* args)
{
    PyObject* onebyone = Py_False;

    if (!PyArg_ParseTuple(args, "|O!", &PyBool_Type, &onebyone)) {
        return nullptr;
    }

    this->getSketchObjectPtr()->makeMissingVerticalHorizontal(Base::asBoolean(onebyone));

    Py_Return;
}

PyObject* SketchObjectPy::makeMissingEquality(PyObject* args)
{
    PyObject* onebyone = Py_True;

    if (!PyArg_ParseTuple(args, "|O!", &PyBool_Type, &onebyone)) {
        return nullptr;
    }

    this->getSketchObjectPtr()->makeMissingEquality(Base::asBoolean(onebyone));

    Py_Return;
}

PyObject* SketchObjectPy::evaluateConstraints() const
{
    bool ok = this->getSketchObjectPtr()->evaluateConstraints();
    return Py::new_reference_to(Py::Boolean(ok));
}

PyObject* SketchObjectPy::validateConstraints()
{
    this->getSketchObjectPtr()->validateConstraints();
    Py_Return;
}

PyObject* SketchObjectPy::autoRemoveRedundants(PyObject* args)
{
    PyObject* updategeo = Py_True;

    if (!PyArg_ParseTuple(args, "|O!", &PyBool_Type, &updategeo)) {
        return nullptr;
    }

    this->getSketchObjectPtr()->autoRemoveRedundants(
        Base::asBoolean(updategeo) ? DeleteOption::UpdateGeometry : DeleteOption::NoFlag
    );

    Py_Return;
}

PyObject* SketchObjectPy::toPythonCommands(PyObject* args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }

    auto sketch = this->getSketchObjectPtr();

    std::string geometry = PythonConverter::convert("ActiveSketch", sketch->Geometry.getValues());
    std::string constraints = PythonConverter::convert("ActiveSketch", sketch->Constraints.getValues());

    auto geometrymulti = PythonConverter::multiLine(std::move(geometry));
    auto constraintmulti = PythonConverter::multiLine(std::move(constraints));

    size_t numelements = geometrymulti.size() + constraintmulti.size();

    Py::Tuple tuple(numelements);

    std::size_t i = 0;

    for (const auto& str : geometrymulti) {
        tuple.setItem(i, Py::String(str));
        i++;
    }

    for (const auto& str : constraintmulti) {
        tuple.setItem(i, Py::String(str));
        i++;
    }

    return Py::new_reference_to(tuple);
}


Py::List SketchObjectPy::getMissingPointOnPointConstraints() const
{
    std::vector<ConstraintIds> constraints
        = this->getSketchObjectPtr()->getMissingPointOnPointConstraints();

    Py::List list;
    for (auto c : constraints) {
        Py::Tuple t(5);
        t.setItem(0, Py::Long(c.First));
        t.setItem(
            1,
            Py::Long(
                ((c.FirstPos == Sketcher::PointPos::none)        ? 0
                     : (c.FirstPos == Sketcher::PointPos::start) ? 1
                     : (c.FirstPos == Sketcher::PointPos::end)   ? 2
                                                                 : 3)
            )
        );
        t.setItem(2, Py::Long(c.Second));
        t.setItem(
            3,
            Py::Long(
                ((c.SecondPos == Sketcher::PointPos::none)        ? 0
                     : (c.SecondPos == Sketcher::PointPos::start) ? 1
                     : (c.SecondPos == Sketcher::PointPos::end)   ? 2
                                                                  : 3)
            )
        );
        t.setItem(4, Py::Long(c.Type));
        list.append(t);
    }
    return list;
}

void SketchObjectPy::setMissingPointOnPointConstraints(Py::List arg)
{
    std::vector<ConstraintIds> constraints;

    auto checkpos = [](Py::Tuple& t, int i) {
        auto checkitem = [](Py::Tuple& t, int i, int val) {
            return long(Py::Long(t.getItem(i))) == val;
        };
        return (
            checkitem(t, i, 0)
                ? Sketcher::PointPos::none
                : (checkitem(t, i, 1)
                       ? Sketcher::PointPos::start
                       : (checkitem(t, i, 2) ? Sketcher::PointPos::end : Sketcher::PointPos::mid))
        );
    };

    for (const auto& ti : arg) {
        Py::Tuple t(ti);
        ConstraintIds c;
        c.First = static_cast<long>(Py::Long(t.getItem(0)));
        c.FirstPos = checkpos(t, 1);
        c.Second = static_cast<long>(Py::Long(t.getItem(2)));
        c.SecondPos = checkpos(t, 3);
        c.Type = static_cast<Sketcher::ConstraintType>(static_cast<long>(Py::Long(t.getItem(4))));

        constraints.push_back(c);
    }

    this->getSketchObjectPtr()->setMissingPointOnPointConstraints(constraints);
}

Py::List SketchObjectPy::getMissingVerticalHorizontalConstraints() const
{
    std::vector<ConstraintIds> constraints
        = this->getSketchObjectPtr()->getMissingVerticalHorizontalConstraints();

    Py::List list;
    for (auto c : constraints) {
        Py::Tuple t(5);
        t.setItem(0, Py::Long(c.First));
        t.setItem(
            1,
            Py::Long(
                ((c.FirstPos == Sketcher::PointPos::none)        ? 0
                     : (c.FirstPos == Sketcher::PointPos::start) ? 1
                     : (c.FirstPos == Sketcher::PointPos::end)   ? 2
                                                                 : 3)
            )
        );
        t.setItem(2, Py::Long(c.Second));
        t.setItem(
            3,
            Py::Long(
                ((c.SecondPos == Sketcher::PointPos::none)        ? 0
                     : (c.SecondPos == Sketcher::PointPos::start) ? 1
                     : (c.SecondPos == Sketcher::PointPos::end)   ? 2
                                                                  : 3)
            )
        );
        t.setItem(4, Py::Long(c.Type));
        list.append(t);
    }
    return list;
}

void SketchObjectPy::setMissingVerticalHorizontalConstraints(Py::List arg)
{
    std::vector<ConstraintIds> constraints;

    auto checkpos = [](Py::Tuple& t, int i) {
        auto checkitem = [](Py::Tuple& t, int i, int val) {
            return long(Py::Long(t.getItem(i))) == val;
        };
        return (
            checkitem(t, i, 0)
                ? Sketcher::PointPos::none
                : (checkitem(t, i, 1)
                       ? Sketcher::PointPos::start
                       : (checkitem(t, i, 2) ? Sketcher::PointPos::end : Sketcher::PointPos::mid))
        );
    };

    for (const auto& ti : arg) {
        Py::Tuple t(ti);
        ConstraintIds c;
        c.First = static_cast<long>(Py::Long(t.getItem(0)));
        c.FirstPos = checkpos(t, 1);
        c.Second = static_cast<long>(Py::Long(t.getItem(2)));
        c.SecondPos = checkpos(t, 3);
        c.Type = static_cast<Sketcher::ConstraintType>(static_cast<long>(Py::Long(t.getItem(4))));

        constraints.push_back(c);
    }

    this->getSketchObjectPtr()->setMissingVerticalHorizontalConstraints(constraints);
}

Py::List SketchObjectPy::getMissingLineEqualityConstraints() const
{
    std::vector<ConstraintIds> constraints
        = this->getSketchObjectPtr()->getMissingLineEqualityConstraints();

    Py::List list;
    for (auto c : constraints) {
        Py::Tuple t(4);
        t.setItem(0, Py::Long(c.First));
        t.setItem(
            1,
            Py::Long(
                ((c.FirstPos == Sketcher::PointPos::none)        ? 0
                     : (c.FirstPos == Sketcher::PointPos::start) ? 1
                     : (c.FirstPos == Sketcher::PointPos::end)   ? 2
                                                                 : 3)
            )
        );
        t.setItem(2, Py::Long(c.Second));
        t.setItem(
            3,
            Py::Long(
                ((c.SecondPos == Sketcher::PointPos::none)        ? 0
                     : (c.SecondPos == Sketcher::PointPos::start) ? 1
                     : (c.SecondPos == Sketcher::PointPos::end)   ? 2
                                                                  : 3)
            )
        );
        list.append(t);
    }
    return list;
}

void SketchObjectPy::setMissingLineEqualityConstraints(Py::List arg)
{
    std::vector<ConstraintIds> constraints;

    auto checkpos = [](Py::Tuple& t, int i) {
        auto checkitem = [](Py::Tuple& t, int i, int val) {
            return long(Py::Long(t.getItem(i))) == val;
        };
        return (
            checkitem(t, i, 0)
                ? Sketcher::PointPos::none
                : (checkitem(t, i, 1)
                       ? Sketcher::PointPos::start
                       : (checkitem(t, i, 2) ? Sketcher::PointPos::end : Sketcher::PointPos::mid))
        );
    };

    for (const auto& ti : arg) {
        Py::Tuple t(ti);
        ConstraintIds c;
        c.First = (long)Py::Long(t.getItem(0));
        c.FirstPos = checkpos(t, 1);
        c.Second = (long)Py::Long(t.getItem(2));
        c.SecondPos = checkpos(t, 3);
        c.Type = Sketcher::Equal;

        constraints.push_back(c);
    }

    this->getSketchObjectPtr()->setMissingLineEqualityConstraints(constraints);
}

Py::List SketchObjectPy::getMissingRadiusConstraints() const
{
    std::vector<ConstraintIds> constraints = this->getSketchObjectPtr()->getMissingRadiusConstraints();

    Py::List list;
    for (auto c : constraints) {
        Py::Tuple t(4);
        t.setItem(0, Py::Long(c.First));
        t.setItem(
            1,
            Py::Long(
                ((c.FirstPos == Sketcher::PointPos::none)        ? 0
                     : (c.FirstPos == Sketcher::PointPos::start) ? 1
                     : (c.FirstPos == Sketcher::PointPos::end)   ? 2
                                                                 : 3)
            )
        );
        t.setItem(2, Py::Long(c.Second));
        t.setItem(
            3,
            Py::Long(
                ((c.SecondPos == Sketcher::PointPos::none)        ? 0
                     : (c.SecondPos == Sketcher::PointPos::start) ? 1
                     : (c.SecondPos == Sketcher::PointPos::end)   ? 2
                                                                  : 3)
            )
        );
        list.append(t);
    }
    return list;
}

void SketchObjectPy::setMissingRadiusConstraints(Py::List arg)
{
    std::vector<ConstraintIds> constraints;

    auto checkpos = [](Py::Tuple& t, int i) {
        auto checkitem = [](Py::Tuple& t, int i, int val) {
            return long(Py::Long(t.getItem(i))) == val;
        };
        return (
            checkitem(t, i, 0)
                ? Sketcher::PointPos::none
                : (checkitem(t, i, 1)
                       ? Sketcher::PointPos::start
                       : (checkitem(t, i, 2) ? Sketcher::PointPos::end : Sketcher::PointPos::mid))
        );
    };

    for (const auto& ti : arg) {
        Py::Tuple t(ti);
        ConstraintIds c;
        c.First = (long)Py::Long(t.getItem(0));
        c.FirstPos = checkpos(t, 1);
        c.Second = (long)Py::Long(t.getItem(2));
        c.SecondPos = checkpos(t, 3);
        c.Type = Sketcher::Equal;

        constraints.push_back(c);
    }

    this->getSketchObjectPtr()->setMissingRadiusConstraints(constraints);
}

PyObject* SketchObjectPy::getGeometryWithDependentParameters(PyObject* args)
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }

    std::vector<std::pair<int, PointPos>> geometrymap;

    this->getSketchObjectPtr()->getGeometryWithDependentParameters(geometrymap);

    Py::List list;
    for (auto pair : geometrymap) {
        Py::Tuple t(2);
        t.setItem(0, Py::Long(pair.first));
        t.setItem(
            1,
            Py::Long(
                ((pair.second == Sketcher::PointPos::none)        ? 0
                     : (pair.second == Sketcher::PointPos::start) ? 1
                     : (pair.second == Sketcher::PointPos::end)   ? 2
                                                                  : 3)
            )
        );
        list.append(t);
    }
    return Py::new_reference_to(list);
}

Py::List SketchObjectPy::getOpenVertices() const
{
    std::vector<Base::Vector3d> points = this->getSketchObjectPtr()->getOpenVertices();

    Py::List list;
    for (auto p : points) {
        Py::Tuple t(3);
        t.setItem(0, Py::Float(p.x));
        t.setItem(1, Py::Float(p.y));
        t.setItem(2, Py::Float(p.z));
        list.append(t);
    }
    return list;
}

Py::Long SketchObjectPy::getConstraintCount() const
{
    return Py::Long(this->getSketchObjectPtr()->Constraints.getSize());
}

Py::Long SketchObjectPy::getGeometryCount() const
{
    return Py::Long(this->getSketchObjectPtr()->Geometry.getSize());
}

Py::Long SketchObjectPy::getAxisCount() const
{
    return Py::Long(this->getSketchObjectPtr()->getAxisCount());
}


Py::List SketchObjectPy::getGeometryFacadeList() const
{
    Py::List list;

    for (int i = 0; i < getSketchObjectPtr()->Geometry.getSize(); i++) {

        // we create a python copy and add it to the list
        std::unique_ptr<GeometryFacade> geofacade = GeometryFacade::getFacade(
            getSketchObjectPtr()->Geometry[i]->clone()
        );
        geofacade->setOwner(true);

        Py::Object gfp = Py::Object(new GeometryFacadePy(geofacade.release()), true);

        list.append(gfp);
    }
    return list;
}

void SketchObjectPy::setGeometryFacadeList(Py::List value)
{
    std::vector<Part::Geometry*> list;
    list.reserve(value.size());

    for (const auto& ti : value) {
        if (PyObject_TypeCheck(ti.ptr(), &(GeometryFacadePy::Type))) {

            GeometryFacadePy* gfp = static_cast<GeometryFacadePy*>(ti.ptr());

            GeometryFacade* gf = gfp->getGeometryFacadePtr();

            Part::Geometry* geo = gf->getGeometry()->clone();

            list.push_back(geo);
        }
    }

    getSketchObjectPtr()->Geometry.setValues(std::move(list));
}

PyObject* SketchObjectPy::getGeometryId(PyObject* args)
{
    int Index;
    if (!PyArg_ParseTuple(args, "i", &Index)) {
        return nullptr;
    }

    long Id;

    if (this->getSketchObjectPtr()->getGeometryId(Index, Id)) {
        std::stringstream str;
        str << "Not able to get geometry Id of a geometry with the given index: " << Index;
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        Py_Return;
    }

    return Py::new_reference_to(Py::Long(Id));
}

PyObject* SketchObjectPy::setGeometryId(PyObject* args)
{
    int Index;
    long Id;
    if (!PyArg_ParseTuple(args, "il", &Index, &Id)) {
        return nullptr;
    }

    if (this->getSketchObjectPtr()->setGeometryId(Index, Id)) {
        std::stringstream str;
        str << "Not able to set geometry Id of a geometry with the given index: " << Index;
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    Py_Return;
}
PyObject* SketchObjectPy::setGeometryIds(PyObject* args)
{
    PyObject* pyList;

    // Parse arguments: list of pairs, Base::VectorPy, optional relative flag
    if (!PyArg_ParseTuple(args, "O!", &PyList_Type, &pyList)) {
        return nullptr;
    }

    // Convert Python list to std::vector<std::pair<int, long>>
    std::vector<std::pair<int, long>> geoIdsToIds;
    Py_ssize_t listSize = PyList_Size(pyList);

    for (Py_ssize_t i = 0; i < listSize; ++i) {
        PyObject* pyPair = PyList_GetItem(pyList, i);  // Borrowed reference

        if (!PyTuple_Check(pyPair) || PyTuple_Size(pyPair) != 2) {
            PyErr_SetString(PyExc_ValueError, "List must contain pairs (geoId, id).");
            return nullptr;
        }

        int geoId = PyLong_AsLong(PyTuple_GetItem(pyPair, 0));
        long id = PyLong_AsLong(PyTuple_GetItem(pyPair, 1));

        if (PyErr_Occurred()) {
            PyErr_SetString(PyExc_ValueError, "Invalid geoId or id in the list.");
            return nullptr;
        }

        geoIdsToIds.emplace_back(geoId, id);
    }

    // Call the C++ method
    if (this->getSketchObjectPtr()->setGeometryIds(geoIdsToIds)) {
        std::stringstream str;
        str << "Not able to set geometry Ids of geometries with the given indices: ";
        PyErr_SetString(PyExc_ValueError, str.str().c_str());
        return nullptr;
    }

    Py_Return;
}


Py::Long SketchObjectPy::getDoF() const
{
    auto dofs = this->getSketchObjectPtr()->getLastDoF();

    return Py::Long(dofs);
}

Py::List SketchObjectPy::getConflictingConstraints() const
{
    auto conflictinglist = this->getSketchObjectPtr()->getLastConflicting();

    Py::List conflicting;

    for (auto cid : conflictinglist) {
        conflicting.append(Py::Long(cid));
    }

    return conflicting;
}

Py::List SketchObjectPy::getRedundantConstraints() const
{
    auto redundantlist = this->getSketchObjectPtr()->getLastRedundant();

    Py::List redundant;

    for (auto cid : redundantlist) {
        redundant.append(Py::Long(cid));
    }

    return redundant;
}

Py::List SketchObjectPy::getPartiallyRedundantConstraints() const
{
    auto redundantlist = this->getSketchObjectPtr()->getLastPartiallyRedundant();

    Py::List redundant;

    for (auto cid : redundantlist) {
        redundant.append(Py::Long(cid));
    }

    return redundant;
}

Py::List SketchObjectPy::getMalformedConstraints() const
{
    auto malformedlist = this->getSketchObjectPtr()->getLastMalformedConstraints();

    Py::List malformed;

    for (auto cid : malformedlist) {
        malformed.append(Py::Long(cid));
    }

    return malformed;
}

PyObject* SketchObjectPy::getCustomAttributes(const char* /*attr*/) const
{
    return nullptr;
}

int SketchObjectPy::setCustomAttributes(const char* attr, PyObject* obj)
{
    // search in PropertyList
    App::Property* prop = getSketchObjectPtr()->getPropertyByName(attr);
    if (prop) {
        // Read-only attributes must not be set over its Python interface
        short Type = getSketchObjectPtr()->getPropertyType(prop);
        if (Type & App::Prop_ReadOnly) {
            std::stringstream s;
            s << "Object attribute '" << attr << "' is read-only";
            throw Py::AttributeError(s.str());
        }

        prop->setPyObject(obj);

        if (strcmp(attr, "Geometry") == 0) {
            getSketchObjectPtr()->rebuildVertexIndex();
        }

        return 1;
    }

    return 0;
}
