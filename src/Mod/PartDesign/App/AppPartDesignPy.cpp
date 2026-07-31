// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2008 Jürgen Riegel <juergen.riegel@web.de>              *
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

#include <utility>
#include <vector>

#include <App/DocumentObjectPy.h>
#include <App/Part.h>
#include <Base/GeometryPyCXX.h>
#include <Base/Interpreter.h>
#include <Base/Tools.h>
#include <Base/VectorPy.h>
#include <Mod/Part/App/TopoShapePy.h>

#include "Body.h"
#include "DesignFeature.h"
#include "DesignModel.h"
#include "FeatureHole.h"

namespace
{

constexpr const char* designEditCapsuleName = "PartDesign.DesignOperationEdit";

void deleteDesignEditCapsule(PyObject* capsule)
{
    auto* edit = static_cast<PartDesign::DesignOperationEdit*>(
        PyCapsule_GetPointer(capsule, designEditCapsuleName)
    );
    if (!edit) {
        PyErr_Clear();
        return;
    }
    delete edit;
}

App::DocumentObject* documentObjectFromPython(PyObject* object, const char* argument)
{
    if (!object || !PyObject_TypeCheck(object, &App::DocumentObjectPy::Type)) {
        throw Py::TypeError(std::string(argument) + " must be a document object");
    }
    auto* result = static_cast<App::DocumentObjectPy*>(object)->getDocumentObjectPtr();
    if (!result || !result->getDocument()) {
        throw Py::RuntimeError(std::string(argument) + " is no longer in a document");
    }
    return result;
}

PartDesign::DesignOperationEdit* designEditFromCapsule(PyObject* capsule)
{
    auto* edit = static_cast<PartDesign::DesignOperationEdit*>(
        PyCapsule_GetPointer(capsule, designEditCapsuleName)
    );
    if (!edit) {
        throw Py::TypeError("edit must be an active Design operation edit");
    }
    if (!edit->operation) {
        throw Py::RuntimeError("This Design operation edit was already finalized");
    }
    return edit;
}

}  // namespace


namespace PartDesign
{
class Module: public Py::ExtensionModule<Module>
{
public:
    Module()
        : Py::ExtensionModule<Module>("_PartDesign")
    {
        add_varargs_method("makeFilletArc", &Module::makeFilletArc, "makeFilletArc(...) -- Fillet arc.");
        add_noargs_method(
            "getHoleThreadCatalog",
            &Module::getHoleThreadCatalog,
            "Return the native Hole thread catalog without creating a document feature."
        );
        add_varargs_method(
            "beginDesignOperationEdit",
            &Module::beginDesignOperationEdit,
            "Capture the exact persisted state of one Design operation before editing."
        );
        add_varargs_method(
            "initializeDesignDefinition",
            &Module::initializeDesignDefinition,
            "Assign persistent Design identity to one new global reusable definition."
        );
        add_varargs_method(
            "resolveDesignDefinitionReference",
            &Module::resolveDesignDefinitionReference,
            "Resolve a selected object to the exact earlier state consumed by a reusable "
            "definition."
        );
        add_varargs_method(
            "resolveDesignDefinitionSubelementReference",
            &Module::resolveDesignDefinitionSubelementReference,
            "Resolve selected faces, edges, or vertices to canonical names on the exact "
            "earlier state consumed by a reusable definition."
        );
        add_varargs_method(
            "initializeDesignBodyFromLegacyFeature",
            &Module::initializeDesignBodyFromLegacyFeature,
            "Promote one legacy Body tip into an immutable initial Design state."
        );
        add_varargs_method(
            "finalizeDesignDefinition",
            &Module::finalizeDesignDefinition,
            "Validate and publish one global reusable definition in History."
        );
        add_varargs_method(
            "setDesignOperationTargets",
            &Module::setDesignOperationTargets,
            "Set New Body, Join, Cut, or Intersect targets using the edit's saved frames."
        );
        add_varargs_method(
            "setDesignFeaturePatternTargets",
            &Module::setDesignFeaturePatternTargets,
            "Repeat one earlier Design feature on explicit target Bodies."
        );
        add_varargs_method(
            "setDesignBodyPatternSource",
            &Module::setDesignBodyPatternSource,
            "Copy one exact Body state into independently identified Pattern outputs."
        );
        add_varargs_method(
            "setDesignCloneSource",
            &Module::setDesignCloneSource,
            "Copy one exact Body state into one independently identified Body."
        );
        add_varargs_method(
            "setDesignScriptOutputs",
            &Module::setDesignScriptOutputs,
            "Configure stable multi-Body outputs for one accepted VibeScript program."
        );
        add_varargs_method(
            "setDesignCombineBodies",
            &Module::setDesignCombineBodies,
            "Set one explicit result Body and the tool Bodies for a Design Combine."
        );
        add_varargs_method(
            "setDesignSplitDefinition",
            &Module::setDesignSplitDefinition,
            "Set one explicit source Body and exact splitting definitions; return unassigned "
            "regions."
        );
        add_varargs_method(
            "assignDesignSplitRegions",
            &Module::assignDesignSplitRegions,
            "Choose which Split region keeps the source Body identity."
        );
        add_varargs_method(
            "setDesignSeparateDefinition",
            &Module::setDesignSeparateDefinition,
            "Separate one reusable multi-solid definition into stable new Bodies."
        );
        add_varargs_method(
            "finalizeDesignOperationEdit",
            &Module::finalizeDesignOperationEdit,
            "Validate and atomically publish one Design operation edit."
        );
        add_varargs_method(
            "removeDesignOperation",
            &Module::removeDesignOperation,
            "Remove one global Design operation and reconcile every Body output."
        );
        add_varargs_method(
            "validateDesign",
            &Module::validateDesign,
            "Validate the complete Design identity, History, state, and publication graph."
        );
        initialize("This module is the PartDesign module.");  // register with Python
    }

private:
    Py::Object beginDesignOperationEdit(const Py::Tuple& args)
    {
        PyObject* operationObject = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O", &operationObject)) {
            throw Py::Exception();
        }
        auto* operation = documentObjectFromPython(operationObject, "operation");
        DesignOperationEdit snapshot;
        try {
            snapshot = DesignModel::beginOperationEdit(*operation);
        }
        catch (const Base::Exception& error) {
            throw Py::RuntimeError(error.what());
        }
        auto* edit = new DesignOperationEdit(std::move(snapshot));
        PyObject* capsule = PyCapsule_New(edit, designEditCapsuleName, deleteDesignEditCapsule);
        if (!capsule) {
            delete edit;
            throw Py::Exception();
        }
        return Py::Object(capsule, true);
    }

    Py::Object setDesignOperationTargets(const Py::Tuple& args)
    {
        PyObject* editObject = nullptr;
        const char* resultMode = nullptr;
        PyObject* bodyObjects = nullptr;
        PyObject* componentObject = Py_None;
        PyObject* allowIncompleteObject = Py_False;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "OsO|OO",
                &editObject,
                &resultMode,
                &bodyObjects,
                &componentObject,
                &allowIncompleteObject
            )) {
            throw Py::Exception();
        }

        auto* edit = designEditFromCapsule(editObject);
        Py::Sequence bodySequence(bodyObjects);
        std::vector<Body*> bodies;
        bodies.reserve(static_cast<std::size_t>(bodySequence.size()));
        for (const auto& item : bodySequence) {
            auto* body = freecad_cast<Body*>(documentObjectFromPython(item.ptr(), "body target"));
            if (!body) {
                throw Py::TypeError("Every body target must be a PartDesign Body");
            }
            bodies.push_back(body);
        }

        App::Part* component = nullptr;
        if (componentObject != Py_None) {
            component = freecad_cast<App::Part*>(
                documentObjectFromPython(componentObject, "destination component")
            );
            if (!component || component->Type.getStrValue() != "Component") {
                throw Py::TypeError("destination component must be a Design Component");
            }
        }

        try {
            DesignModel::setOperationTargets(
                *edit,
                resultMode,
                bodies,
                component,
                Base::asBoolean(allowIncompleteObject)
            );
        }
        catch (const Base::Exception& error) {
            throw Py::RuntimeError(error.what());
        }
        return Py::None();
    }

    Py::Object setDesignFeaturePatternTargets(const Py::Tuple& args)
    {
        PyObject* editObject = nullptr;
        PyObject* sourceObject = nullptr;
        PyObject* bodyObjects = nullptr;
        PyObject* allowIncompleteObject = Py_False;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "OOO|O",
                &editObject,
                &sourceObject,
                &bodyObjects,
                &allowIncompleteObject
            )) {
            throw Py::Exception();
        }

        auto* edit = designEditFromCapsule(editObject);
        auto* source = documentObjectFromPython(sourceObject, "Feature Pattern source");
        Py::Sequence bodySequence(bodyObjects);
        std::vector<Body*> bodies;
        bodies.reserve(static_cast<std::size_t>(bodySequence.size()));
        for (const auto& item : bodySequence) {
            auto* body = freecad_cast<Body*>(
                documentObjectFromPython(item.ptr(), "Feature Pattern target")
            );
            if (!body) {
                throw Py::TypeError("Every Feature Pattern target must be a PartDesign Body");
            }
            bodies.push_back(body);
        }

        try {
            DesignModel::setFeaturePatternTargets(
                *edit,
                *source,
                bodies,
                Base::asBoolean(allowIncompleteObject)
            );
        }
        catch (const Base::Exception& error) {
            throw Py::RuntimeError(error.what());
        }
        return Py::None();
    }

    Py::Object setDesignBodyPatternSource(const Py::Tuple& args)
    {
        PyObject* editObject = nullptr;
        PyObject* sourceBodyObject = nullptr;
        unsigned long generatedCopyCount = 0;
        if (!PyArg_ParseTuple(args.ptr(), "OOk", &editObject, &sourceBodyObject, &generatedCopyCount)) {
            throw Py::Exception();
        }

        auto* edit = designEditFromCapsule(editObject);
        auto* sourceBody = freecad_cast<Body*>(
            documentObjectFromPython(sourceBodyObject, "Body Pattern source")
        );
        if (!sourceBody) {
            throw Py::TypeError("Body Pattern source must be a PartDesign Body");
        }

        try {
            DesignModel::setBodyPatternSource(
                *edit,
                *sourceBody,
                static_cast<std::size_t>(generatedCopyCount)
            );
        }
        catch (const Base::Exception& error) {
            throw Py::RuntimeError(error.what());
        }
        return Py::None();
    }

    Py::Object setDesignCloneSource(const Py::Tuple& args)
    {
        PyObject* editObject = nullptr;
        PyObject* sourceBodyObject = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "OO", &editObject, &sourceBodyObject)) {
            throw Py::Exception();
        }

        auto* edit = designEditFromCapsule(editObject);
        auto* sourceBody = freecad_cast<Body*>(
            documentObjectFromPython(sourceBodyObject, "Clone source")
        );
        if (!sourceBody) {
            throw Py::TypeError("Clone source must be a PartDesign Body");
        }

        try {
            DesignModel::setCloneSource(*edit, *sourceBody);
        }
        catch (const Base::Exception& error) {
            throw Py::RuntimeError(error.what());
        }
        return Py::None();
    }

    Py::Object setDesignScriptOutputs(const Py::Tuple& args)
    {
        PyObject* editObject = nullptr;
        const char* programObjectName = nullptr;
        const char* programId = nullptr;
        const char* revision = nullptr;
        PyObject* outputKeyObjects = nullptr;
        PyObject* outputLabelObjects = nullptr;
        PyObject* outputShapeObjects = nullptr;
        PyObject* adoptedBodyObjects = nullptr;
        PyObject* programOutputKeyObjects = nullptr;
        PyObject* programOutputTypeObjects = nullptr;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "OsssOOOO|OO",
                &editObject,
                &programObjectName,
                &programId,
                &revision,
                &outputKeyObjects,
                &outputLabelObjects,
                &outputShapeObjects,
                &adoptedBodyObjects,
                &programOutputKeyObjects,
                &programOutputTypeObjects
            )) {
            throw Py::Exception();
        }
        if ((programOutputKeyObjects == nullptr) != (programOutputTypeObjects == nullptr)) {
            throw Py::TypeError("program output keys and types must be supplied together");
        }

        auto* edit = designEditFromCapsule(editObject);
        const auto stringsFromSequence = [](PyObject* object, const char* argument) {
            Py::Sequence sequence(object);
            std::vector<std::string> values;
            values.reserve(static_cast<std::size_t>(sequence.size()));
            for (const auto& item : sequence) {
                if (!PyUnicode_Check(item.ptr())) {
                    throw Py::TypeError(std::string("Every ") + argument + " must be a string");
                }
                const char* value = PyUnicode_AsUTF8(item.ptr());
                if (!value) {
                    throw Py::Exception();
                }
                values.emplace_back(value);
            }
            return values;
        };
        const auto outputKeys = stringsFromSequence(outputKeyObjects, "VibeScript output key");
        const auto outputLabels = stringsFromSequence(outputLabelObjects, "VibeScript output label");
        const auto programOutputKeys = programOutputKeyObjects
            ? stringsFromSequence(programOutputKeyObjects, "published VibeScript output key")
            : outputKeys;
        const auto programOutputTypes = programOutputTypeObjects
            ? stringsFromSequence(programOutputTypeObjects, "published VibeScript output type")
            : std::vector<std::string>(outputKeys.size(), "solid");

        Py::Sequence shapeSequence(outputShapeObjects);
        std::vector<Part::TopoShape> outputShapes;
        outputShapes.reserve(static_cast<std::size_t>(shapeSequence.size()));
        for (const auto& item : shapeSequence) {
            if (!PyObject_TypeCheck(item.ptr(), &Part::TopoShapePy::Type)) {
                throw Py::TypeError("Every VibeScript Body output must be a Part Shape");
            }
            outputShapes.push_back(*static_cast<Part::TopoShapePy*>(item.ptr())->getTopoShapePtr());
        }

        Py::Sequence adoptedSequence(adoptedBodyObjects);
        std::vector<Body*> adoptedBodies;
        adoptedBodies.reserve(static_cast<std::size_t>(adoptedSequence.size()));
        for (const auto& item : adoptedSequence) {
            if (item.ptr() == Py_None) {
                adoptedBodies.push_back(nullptr);
                continue;
            }
            auto* body = freecad_cast<Body*>(
                documentObjectFromPython(item.ptr(), "adopted VibeScript output Body")
            );
            if (!body) {
                throw Py::TypeError(
                    "Every adopted VibeScript output must be a PartDesign Body or None"
                );
            }
            adoptedBodies.push_back(body);
        }

        try {
            DesignModel::setScriptOutputs(
                *edit,
                programObjectName,
                programId,
                revision,
                outputKeys,
                outputLabels,
                outputShapes,
                adoptedBodies,
                programOutputKeys,
                programOutputTypes
            );
        }
        catch (const Base::Exception& error) {
            throw Py::RuntimeError(error.what());
        }
        catch (const std::exception& error) {
            throw Py::RuntimeError(error.what());
        }
        return Py::None();
    }

    Py::Object setDesignCombineBodies(const Py::Tuple& args)
    {
        PyObject* editObject = nullptr;
        const char* resultMode = nullptr;
        PyObject* resultBodyObject = nullptr;
        PyObject* toolBodyObjects = nullptr;
        PyObject* keepToolsObject = nullptr;
        PyObject* allowIncompleteObject = Py_False;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "OsOOO|O",
                &editObject,
                &resultMode,
                &resultBodyObject,
                &toolBodyObjects,
                &keepToolsObject,
                &allowIncompleteObject
            )) {
            throw Py::Exception();
        }

        auto* edit = designEditFromCapsule(editObject);
        auto* resultBody = freecad_cast<Body*>(
            documentObjectFromPython(resultBodyObject, "Combine result Body")
        );
        if (!resultBody) {
            throw Py::TypeError("Combine result Body must be a PartDesign Body");
        }

        Py::Sequence toolSequence(toolBodyObjects);
        std::vector<Body*> toolBodies;
        toolBodies.reserve(static_cast<std::size_t>(toolSequence.size()));
        for (const auto& item : toolSequence) {
            auto* body = freecad_cast<Body*>(documentObjectFromPython(item.ptr(), "Combine tool Body")
            );
            if (!body) {
                throw Py::TypeError("Every Combine tool must be a PartDesign Body");
            }
            toolBodies.push_back(body);
        }

        try {
            DesignModel::setCombineBodies(
                *edit,
                resultMode,
                *resultBody,
                toolBodies,
                Base::asBoolean(keepToolsObject),
                Base::asBoolean(allowIncompleteObject)
            );
        }
        catch (const Base::Exception& error) {
            throw Py::RuntimeError(error.what());
        }
        return Py::None();
    }

    Py::Object setDesignSplitDefinition(const Py::Tuple& args)
    {
        PyObject* editObject = nullptr;
        PyObject* sourceBodyObject = nullptr;
        PyObject* splitterObjects = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "OOO", &editObject, &sourceBodyObject, &splitterObjects)) {
            throw Py::Exception();
        }

        auto* edit = designEditFromCapsule(editObject);
        auto* sourceBody = freecad_cast<Body*>(
            documentObjectFromPython(sourceBodyObject, "Split source Body")
        );
        if (!sourceBody) {
            throw Py::TypeError("Split source Body must be a PartDesign Body");
        }

        Py::Sequence splitterSequence(splitterObjects);
        std::vector<App::PropertyLinkSubList::SubSet> splitters;
        splitters.reserve(static_cast<std::size_t>(splitterSequence.size()));
        for (const auto& item : splitterSequence) {
            if (PyObject_TypeCheck(item.ptr(), &App::DocumentObjectPy::Type)) {
                splitters.emplace_back(
                    documentObjectFromPython(item.ptr(), "Split definition"),
                    std::vector<std::string> {}
                );
                continue;
            }

            if (!PyTuple_Check(item.ptr()) || PyTuple_GET_SIZE(item.ptr()) != 2) {
                throw Py::TypeError("Every Split definition must be a document object or "
                                    "(object, subelements) pair");
            }
            auto* object
                = documentObjectFromPython(PyTuple_GET_ITEM(item.ptr(), 0), "Split definition");
            Py::Sequence subelementSequence(PyTuple_GET_ITEM(item.ptr(), 1));
            std::vector<std::string> subelements;
            subelements.reserve(static_cast<std::size_t>(subelementSequence.size()));
            for (const auto& subelement : subelementSequence) {
                if (!PyUnicode_Check(subelement.ptr())) {
                    throw Py::TypeError("Every Split subelement name must be a string");
                }
                const char* value = PyUnicode_AsUTF8(subelement.ptr());
                if (!value) {
                    throw Py::Exception();
                }
                subelements.emplace_back(value);
            }
            splitters.emplace_back(object, std::move(subelements));
        }

        std::vector<Base::Vector3d> witnesses;
        try {
            witnesses = DesignModel::setSplitDefinition(*edit, *sourceBody, splitters);
        }
        catch (const Base::Exception& error) {
            throw Py::RuntimeError(error.what());
        }
        catch (const std::exception& error) {
            throw Py::RuntimeError(error.what());
        }

        Py::List result;
        for (const auto& witness : witnesses) {
            result.append(Py::Vector(witness));
        }
        return result;
    }

    Py::Object assignDesignSplitRegions(const Py::Tuple& args)
    {
        PyObject* editObject = nullptr;
        PyObject* sourceBodyObject = nullptr;
        PyObject* witnessObjects = nullptr;
        Py_ssize_t retainedRegion = -1;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "OOOn",
                &editObject,
                &sourceBodyObject,
                &witnessObjects,
                &retainedRegion
            )) {
            throw Py::Exception();
        }

        auto* edit = designEditFromCapsule(editObject);
        auto* sourceBody = freecad_cast<Body*>(
            documentObjectFromPython(sourceBodyObject, "Split source Body")
        );
        if (!sourceBody) {
            throw Py::TypeError("Split source Body must be a PartDesign Body");
        }
        if (retainedRegion < 0) {
            throw Py::ValueError("retained region must be a non-negative index");
        }

        Py::Sequence witnessSequence(witnessObjects);
        std::vector<Base::Vector3d> witnesses;
        witnesses.reserve(static_cast<std::size_t>(witnessSequence.size()));
        for (const auto& item : witnessSequence) {
            if (!PyObject_TypeCheck(item.ptr(), &Base::VectorPy::Type)) {
                throw Py::TypeError("Every Split region witness must be an App.Vector");
            }
            witnesses.push_back(Py::Vector(item.ptr(), false).toVector());
        }

        try {
            DesignModel::assignSplitRegions(
                *edit,
                *sourceBody,
                witnesses,
                static_cast<std::size_t>(retainedRegion)
            );
        }
        catch (const Base::Exception& error) {
            throw Py::RuntimeError(error.what());
        }
        catch (const std::exception& error) {
            throw Py::RuntimeError(error.what());
        }
        return Py::None();
    }

    Py::Object setDesignSeparateDefinition(const Py::Tuple& args)
    {
        PyObject* editObject = nullptr;
        PyObject* sourceObject = nullptr;
        PyObject* componentObject = Py_None;
        if (!PyArg_ParseTuple(args.ptr(), "OO|O", &editObject, &sourceObject, &componentObject)) {
            throw Py::Exception();
        }

        auto* edit = designEditFromCapsule(editObject);
        auto* source = documentObjectFromPython(sourceObject, "Separate source definition");
        App::Part* component = nullptr;
        if (componentObject != Py_None) {
            component = freecad_cast<App::Part*>(
                documentObjectFromPython(componentObject, "Separate destination Component")
            );
            if (!component || DesignModel::componentId(*component).empty()) {
                throw Py::TypeError("Separate destination must be a VibeCAD Component");
            }
        }

        try {
            DesignModel::setSeparateDefinition(*edit, *source, component);
        }
        catch (const Base::Exception& error) {
            throw Py::RuntimeError(error.what());
        }
        catch (const std::exception& error) {
            throw Py::RuntimeError(error.what());
        }
        return Py::None();
    }

    Py::Object finalizeDesignOperationEdit(const Py::Tuple& args)
    {
        PyObject* editObject = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O", &editObject)) {
            throw Py::Exception();
        }
        auto* edit = designEditFromCapsule(editObject);
        std::vector<Body*> bodies;
        try {
            bodies = DesignModel::finalizeOperation(*edit);
        }
        catch (const Base::Exception& error) {
            throw Py::RuntimeError(error.what());
        }
        catch (const std::exception& error) {
            throw Py::RuntimeError(error.what());
        }
        edit->operation = nullptr;

        Py::List result;
        for (auto* body : bodies) {
            result.append(Py::Object(body->getPyObject(), true));
        }
        return result;
    }

    Py::Object removeDesignOperation(const Py::Tuple& args)
    {
        PyObject* operationObject = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O", &operationObject)) {
            throw Py::Exception();
        }
        auto* operation = documentObjectFromPython(operationObject, "operation");
        std::vector<std::string> removedBodies;
        try {
            removedBodies = DesignModel::removeOperation(*operation);
        }
        catch (const Base::Exception& error) {
            throw Py::RuntimeError(error.what());
        }
        catch (const std::exception& error) {
            throw Py::RuntimeError(error.what());
        }

        Py::List result;
        for (const auto& name : removedBodies) {
            result.append(Py::String(name));
        }
        return result;
    }

    Py::Object validateDesign(const Py::Tuple& args)
    {
        PyObject* object = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O", &object)) {
            throw Py::Exception();
        }
        auto* documentObject = documentObjectFromPython(object, "object");
        try {
            DesignModel::validateDesign(*documentObject->getDocument());
        }
        catch (const Base::Exception& error) {
            throw Py::RuntimeError(error.what());
        }
        return Py::None();
    }

    Py::Object getHoleThreadCatalog()
    {
        Py::List catalog;
        for (const auto& native : Hole::getThreadCatalog()) {
            Py::Dict entry;
            entry["standard"] = Py::String(native.standard);
            Py::List sizes;
            for (const auto& nativeSize : native.sizes) {
                Py::Dict size;
                size["designation"] = Py::String(nativeSize.designation);
                size["diameter_mm"] = Py::Float(nativeSize.diameter);
                size["pitch_mm"] = Py::Float(nativeSize.pitch);
                size["tap_drill_mm"] = Py::Float(nativeSize.TapDrill);
                sizes.append(size);
            }
            auto stringList = [](const std::vector<std::string>& values) {
                Py::List result;
                for (const auto& value : values) {
                    result.append(Py::String(value));
                }
                return result;
            };
            entry["sizes"] = sizes;
            entry["classes"] = stringList(native.classes);
            entry["fits"] = stringList(native.fits);
            entry["hole_cuts"] = stringList(native.holeCuts);
            catalog.append(entry);
        }
        return catalog;
    }

    Py::Object initializeDesignDefinition(const Py::Tuple& args)
    {
        PyObject* definitionObject = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O", &definitionObject)) {
            throw Py::Exception();
        }
        auto* definition = documentObjectFromPython(definitionObject, "Design definition");
        try {
            DesignModel::initializeDefinition(*definition);
        }
        catch (const Base::Exception& error) {
            throw Py::RuntimeError(error.what());
        }
        return Py::None();
    }

    Py::Object resolveDesignDefinitionReference(const Py::Tuple& args)
    {
        PyObject* definitionObject = nullptr;
        PyObject* selectedObject = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "OO", &definitionObject, &selectedObject)) {
            throw Py::Exception();
        }
        auto* definition = documentObjectFromPython(definitionObject, "Design definition");
        auto* selected = documentObjectFromPython(selectedObject, "Selected reference");
        try {
            auto* resolved = DesignModel::resolveDefinitionReference(*definition, *selected);
            return Py::Object(resolved->getPyObject(), true);
        }
        catch (const Base::Exception& error) {
            throw Py::RuntimeError(error.what());
        }
    }

    Py::Object resolveDesignDefinitionSubelementReference(const Py::Tuple& args)
    {
        PyObject* definitionObject = nullptr;
        PyObject* selectedObject = nullptr;
        PyObject* subelementsObject = nullptr;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "OOO",
                &definitionObject,
                &selectedObject,
                &subelementsObject
            )) {
            throw Py::Exception();
        }
        auto* definition =
            documentObjectFromPython(definitionObject, "Design definition");
        auto* selected =
            documentObjectFromPython(selectedObject, "Selected reference");

        Py::Sequence subelementSequence(subelementsObject);
        std::vector<std::string> subelements;
        subelements.reserve(
            static_cast<std::size_t>(subelementSequence.size())
        );
        for (const auto& subelement : subelementSequence) {
            if (!PyUnicode_Check(subelement.ptr())) {
                throw Py::TypeError(
                    "Every Design reference subelement must be a string"
                );
            }
            const char* value = PyUnicode_AsUTF8(subelement.ptr());
            if (!value) {
                throw Py::Exception();
            }
            subelements.emplace_back(value);
        }

        try {
            auto reference =
                DesignModel::resolveDefinitionSubelementReference(
                    *definition,
                    *selected,
                    subelements
                );
            Py::List exactSubelements;
            for (const auto& subelement : reference.subelements) {
                exactSubelements.append(Py::String(subelement));
            }
            return Py::TupleN(
                Py::Object(reference.object->getPyObject(), true),
                exactSubelements
            );
        }
        catch (const Base::Exception& error) {
            throw Py::RuntimeError(error.what());
        }
    }

    Py::Object initializeDesignBodyFromLegacyFeature(const Py::Tuple& args)
    {
        PyObject* bodyObject = nullptr;
        PyObject* featureObject = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "OO", &bodyObject, &featureObject)) {
            throw Py::Exception();
        }
        auto* body = freecad_cast<Body*>(
            documentObjectFromPython(bodyObject, "legacy Body")
        );
        auto* feature = freecad_cast<Part::Feature*>(
            documentObjectFromPython(featureObject, "legacy Body feature")
        );
        if (!body || !feature) {
            throw Py::TypeError(
                "Legacy Design promotion requires one PartDesign Body and one Part feature"
            );
        }
        try {
            auto* state = DesignModel::initializeLegacyBodyState(*body, *feature);
            return Py::Object(state->getPyObject(), true);
        }
        catch (const Base::Exception& error) {
            throw Py::RuntimeError(error.what());
        }
        catch (const std::exception& error) {
            throw Py::RuntimeError(error.what());
        }
    }

    Py::Object finalizeDesignDefinition(const Py::Tuple& args)
    {
        PyObject* definitionObject = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O", &definitionObject)) {
            throw Py::Exception();
        }
        auto* definition = documentObjectFromPython(definitionObject, "Design definition");
        try {
            DesignModel::finalizeDefinition(*definition);
        }
        catch (const Base::Exception& error) {
            throw Py::RuntimeError(error.what());
        }
        return Py::None();
    }

    Py::Object makeFilletArc(const Py::Tuple& args)
    {
        PyObject* pM1;
        PyObject* pP;
        PyObject* pQ;
        PyObject* pN;
        double r2;
        int ccw;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "O!O!O!O!di",
                &(Base::VectorPy::Type),
                &pM1,
                &(Base::VectorPy::Type),
                &pP,
                &(Base::VectorPy::Type),
                &pQ,
                &(Base::VectorPy::Type),
                &pN,
                &r2,
                &ccw
            )) {
            throw Py::Exception();
        }

        Base::Vector3d M1 = Py::Vector(pM1, false).toVector();
        Base::Vector3d P = Py::Vector(pP, false).toVector();
        Base::Vector3d Q = Py::Vector(pQ, false).toVector();
        Base::Vector3d N = Py::Vector(pN, false).toVector();

        Base::Vector3d u = Q - P;
        Base::Vector3d v = P - M1;
        Base::Vector3d b;
        if (ccw) {
            b = u % N;
        }
        else {
            b = N % u;
        }
        b.Normalize();

        double uu = u * u;
        double uv = u * v;
        double r1 = v.Length();

        // distinguish between internal and external fillets
        r2 *= Base::sgn(uv);

        double cc = 2.0 * r2 * (b * v - r1);
        double d = uv * uv - uu * cc;
        if (d < 0) {
            throw Py::RuntimeError("Unable to calculate intersection points");
        }

        double t;
        double t1 = (-uv + sqrt(d)) / uu;
        double t2 = (-uv - sqrt(d)) / uu;

        if (fabs(t1) < fabs(t2)) {
            t = t1;
        }
        else {
            t = t2;
        }

        Base::Vector3d M2 = P + (u * t) + (b * r2);
        Base::Vector3d S1 = (r2 * M1 + r1 * M2) / (r1 + r2);
        Base::Vector3d S2 = M2 - (b * r2);

        Py::Tuple tuple(3);
        tuple.setItem(0, Py::Vector(S1));
        tuple.setItem(1, Py::Vector(S2));
        tuple.setItem(2, Py::Vector(M2));

        return tuple;
    }
};

PyObject* initModule()
{
    return Base::Interpreter().addModule(new Module);
}

}  // namespace PartDesign
