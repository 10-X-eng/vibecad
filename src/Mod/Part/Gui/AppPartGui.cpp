// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2002 Juergen Riegel <juergen.riegel@web.de>             *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU Lesser General Public License (LGPL)    *
 *   as published by the Free Software Foundation; either version 2 of     *
 *   the License, or (at your option) any later version.                   *
 *   for detail see the LICENCE text file.                                 *
 *                                                                         *
 *   FreeCAD is distributed in the hope that it will be useful,            *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
 *   GNU Lesser General Public License for more details.                   *
 *                                                                         *
 *   You should have received a copy of the GNU Library General Public     *
 *   License along with FreeCAD; if not, write to the Free Software        *
 *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
 *   USA                                                                   *
 *                                                                         *
 ***************************************************************************/

#include <algorithm>
#include <QApplication>
#include <QThread>
#include <Gui/FrameBudget.h>
#include <Base/Console.h>
#include <Base/MatrixPy.h>
#include <Base/VectorPy.h>
#include <Mod/Part/App/TopoShapePy.h>
#include "SectionFaceController.h"
#include <Base/Interpreter.h>
#include <Base/PyObjectBase.h>
#include <Base/ServiceProvider.h>

#include <App/DocumentObjectPy.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Document.h>
#include <Gui/Dialogs/DlgPreferencesImp.h>
#include <Gui/WidgetFactory.h>
#include <Gui/Language/Translator.h>

#include <Mod/Part/App/PreviewExtension.h>
#include <Mod/Part/App/BodyBase.h>

#include "AttacherTexts.h"
#include "PropertyEnumAttacherItem.h"
#include "DlgSettings3DViewPartImp.h"
#include "DlgSettingsGeneral.h"
#include "DlgSettingsObjectColor.h"
#include "ModelingSelection.h"
#include "PreviewUpdateScheduler.h"
#include "SoBrepEdgeSet.h"
#include "SoBrepFaceSet.h"
#include "SoBrepPointSet.h"
#include "SoFCShapeObject.h"
#include "ViewProvider.h"
#include "ViewProvider2DObject.h"
#include "ViewProviderAttachExtension.h"
#include "ViewProviderDatum.h"
#include "ViewProviderGridExtension.h"
#include "ViewProviderBoolean.h"
#include "ViewProviderBox.h"
#include "ViewProviderCircleParametric.h"
#include "ViewProviderCompound.h"
#include "ViewProviderConeParametric.h"
#include "ViewProviderCurveNet.h"
#include "ViewProviderCylinderParametric.h"
#include "ViewProviderEllipseParametric.h"
#include "ViewProviderExt.h"
#include "ViewProviderExtrusion.h"
#include "ViewProviderScale.h"
#include "ViewProviderHelixParametric.h"
#include "ViewProviderPrimitive.h"
#include "ViewProviderPython.h"
#include "ViewProviderImport.h"
#include "ViewProviderLineParametric.h"
#include "ViewProviderMirror.h"
#include "ViewProviderPlaneParametric.h"
#include "ViewProviderPointParametric.h"
#include "ViewProviderPreviewExtension.h"
#include "ViewProviderPrism.h"
#include "ViewProviderProjectOnSurface.h"
#include "ViewProviderRegularPolygon.h"
#include "ViewProviderRuledSurface.h"
#include "ViewProviderSphereParametric.h"
#include "ViewProviderSpline.h"
#include "ViewProviderTorusParametric.h"
#include "WorkbenchManipulator.h"


// use a different name to CreateCommand()
void CreatePartCommands();
void CreateSimplePartCommands();
void CreateParamPartCommands();
void CreatePartSelectCommands();

void loadPartResource()
{
    // add resources and reloads the translators
    Q_INIT_RESOURCE(Part);
    Q_INIT_RESOURCE(Part_translation);
    Gui::Translator::instance()->refresh();
}

namespace PartGui
{
class Module: public Py::ExtensionModule<Module>
{
public:
    Module()
        : Py::ExtensionModule<Module>("PartGui")
    {
        add_varargs_method(
            "resolveModelingObject",
            &Module::resolveModelingObject,
            "resolveModelingObject(object) -> object or None\n\n"
            "Return a directly selected Body's exact current modeling state. "
            "Presentation objects are never returned as geometry inputs. "
            "Ordinary objects and App::Link occurrences are returned unchanged."
        );
        add_varargs_method(
            "findModelingBody",
            &Module::findModelingBody,
            "findModelingBody(object) -> object or None\n\n"
            "Return the unique native Body represented by a Body member, "
            "component, or linked presentation."
        );
        add_varargs_method(
            "resolveModelingObjectForBody",
            &Module::resolveModelingObjectForBody,
            "resolveModelingObjectForBody(object, body) -> object or None\n\n"
            "Resolve a visible Body or component presentation to the exact "
            "state that a new operation may safely consume."
        );
        add_varargs_method(
            "isModelingObjectActive",
            &Module::isModelingObjectActive,
            "isModelingObjectActive(object) -> bool\n\n"
            "Return whether the exact object, its Body result, and any linked "
            "definition belong to the current unsuppressed History state."
        );
        add_varargs_method(
            "resolveModelingPresentationObject",
            &Module::resolveModelingPresentationObject,
            "resolveModelingPresentationObject(object) -> object or None\n\n"
            "Return the Body which actually renders a Body-owned modeling "
            "operand. Root objects and App::Link occurrences are returned "
            "unchanged."
        );
        add_varargs_method(
            "setModelingReplacedInputs",
            &Module::setModelingReplacedInputs,
            "setModelingReplacedInputs(result, inputs) -> bool\n\n"
            "Persist exact visible inputs intentionally hidden by a root "
            "modeling result. Body-native results use Tip history and return "
            "False."
        );
        add_varargs_method(
            "publishDesignDefinitionBlock",
            &Module::publishDesignDefinitionBlock,
            "publishDesignDefinitionBlock(outputs) -> None\n\n"
            "Publish newly created root-level outputs as one reusable Design "
            "definition. The final output is the semantic History root."
        );
        add_varargs_method(
            "canStartRetainedModelingTask",
            &Module::canStartRetainedModelingTask,
            "canStartRetainedModelingTask() -> bool\n\n"
            "Return whether a retained Part task can safely own the active "
            "document transaction."
        );
        add_varargs_method("createSectionFaceController", &Module::createSectionFaceController,
                           "createSectionFaceController() -> handle\nCreate an owner-held native section controller.");
        add_varargs_method("requestSectionFaces", &Module::requestSectionFaces,
                           "requestSectionFaces(handle, instances, origin, normal, callback)\n"
                           "Prepare native section faces asynchronously; callback(faces, error) runs on the GUI owner.");
        add_varargs_method("cancelSectionFaces", &Module::cancelSectionFaces,
                           "cancelSectionFaces(handle)\nCancel requests and release callbacks on the GUI owner.");
        add_varargs_method("requestSectionMeshDisplay", &Module::requestSectionMeshDisplay,
                           "Prepare section display from immutable mesh snapshots off the GUI thread.");
        add_varargs_method("requestSectionDisplay", &Module::requestSectionDisplay,
                           "requestSectionDisplay(handle, instances, origin, normal, spacing, callback)\n"
                           "Prepare cap display data on native workers; callback receives a geometry handle and error.");
        add_varargs_method("sectionDisplaySizes", &Module::sectionDisplaySizes,
                           "sectionDisplaySizes(handle) -> triangle, hatch, outline counts");
        add_varargs_method("readSectionDisplay", &Module::readSectionDisplay,
                           "readSectionDisplay(handle, kind, first, count) -> tuple\nRead at most 256 primitives.");
        add_varargs_method("_deferSectionDisplay", &Module::deferSectionDisplay,
                           "Queue a section presentation step through the GUI frame dispatcher.");
        initialize("This module is the PartGui module.");  // register with Python
    }

private:
    using GeometryOwner = std::shared_ptr<const Part::SectionDisplayGeometry>;

    static const GeometryOwner& sectionGeometry(PyObject* object)
    {
        auto* geometry = static_cast<GeometryOwner*>(
            PyCapsule_GetPointer(object, "PartGui.SectionDisplayGeometry"));
        if (!geometry) { throw Py::Exception(); }
        return *geometry;
    }

    Py::Object sectionDisplaySizes(const Py::Tuple& args)
    {
        PyObject* handle;
        if (!PyArg_ParseTuple(args.ptr(), "O", &handle)) { throw Py::Exception(); }
        const auto& geometry = sectionGeometry(handle);
        Py::Tuple sizes(3);
        sizes.setItem(0, Py::Long(geometry ? geometry->triangles.size() : 0));
        sizes.setItem(1, Py::Long(geometry ? geometry->hatch.size() : 0));
        sizes.setItem(2, Py::Long(geometry ? geometry->outlines.size() : 0));
        return sizes;
    }

    Py::Object readSectionDisplay(const Py::Tuple& args)
    {
        PyObject* handle;
        const char* kind;
        Py_ssize_t first, count;
        if (!PyArg_ParseTuple(args.ptr(), "Osnn", &handle, &kind, &first, &count)) {
            throw Py::Exception();
        }
        if (first < 0 || count < 0) { throw Py::ValueError("chunk indices must be nonnegative"); }
        const auto& geometry = sectionGeometry(handle);
        if (!geometry) { return Py::Tuple(); }
        const auto read = [&](const auto& values) {
            const auto begin = static_cast<std::size_t>(first);
            const auto length = begin < values.size()
                ? std::min({static_cast<std::size_t>(count), std::size_t(256), values.size() - begin})
                : 0;
            Py::Tuple result(length);
            for (std::size_t index = 0; index < length; ++index) {
                const auto& primitive = values[begin + index];
                Py::Tuple points(primitive.size());
                for (std::size_t point = 0; point < primitive.size(); ++point) {
                    Py::Tuple xyz(3);
                    xyz.setItem(0, Py::Float(primitive[point].x));
                    xyz.setItem(1, Py::Float(primitive[point].y));
                    xyz.setItem(2, Py::Float(primitive[point].z));
                    points.setItem(point, xyz);
                }
                result.setItem(index, points);
            }
            return result;
        };
        if (strcmp(kind, "triangles") == 0) { return read(geometry->triangles); }
        if (strcmp(kind, "hatch") == 0) { return read(geometry->hatch); }
        if (strcmp(kind, "outlines") == 0) { return read(geometry->outlines); }
        throw Py::ValueError("unknown section primitive kind");
    }

    Py::Object deferSectionDisplay(const Py::Tuple& args)
    {
        PyObject* callback;
        if (!PyArg_ParseTuple(args.ptr(), "O", &callback)) { throw Py::Exception(); }
        if (!qApp || QThread::currentThread() != qApp->thread()) {
            throw Py::RuntimeError("Section display must be queued on the GUI owner");
        }
        if (!PyCallable_Check(callback)) { throw Py::TypeError("callback must be callable"); }
        struct Callback
        {
            PyObject* object;
            explicit Callback(PyObject* object) : object(object) { Py_INCREF(object); }
            ~Callback() { Base::PyGILStateLocker lock; Py_DECREF(object); }
        };
        auto retained = std::make_shared<Callback>(callback);
        return Py::Boolean(Gui::dispatchToGuiFrame([retained] {
            Base::PyGILStateLocker lock;
            try { Py::Callable(retained->object).apply(Py::Tuple()); }
            catch (Py::Exception&) { PyErr_Print(); }
        }));
    }

    static SectionFaceController* sectionController(PyObject* object)
    {
        auto* result = static_cast<SectionFaceController*>(
            PyCapsule_GetPointer(object, "PartGui.SectionFaceController"));
        if (!result) { throw Py::Exception(); }
        return result;
    }

    Py::Object createSectionFaceController(const Py::Tuple& args)
    {
        if (!PyArg_ParseTuple(args.ptr(), "")) { throw Py::Exception(); }
        auto controller = std::make_unique<SectionFaceController>();
        auto* capsule = PyCapsule_New(controller.get(), "PartGui.SectionFaceController", [](PyObject* object) {
            delete static_cast<SectionFaceController*>(
                PyCapsule_GetPointer(object, "PartGui.SectionFaceController"));
        });
        if (!capsule) { throw Py::Exception(); }
        controller.release();
        return Py::asObject(capsule);
    }

    Py::Object cancelSectionFaces(const Py::Tuple& args)
    {
        PyObject* controller;
        if (!PyArg_ParseTuple(args.ptr(), "O", &controller)) { throw Py::Exception(); }
        try { sectionController(controller)->cancel(); }
        catch (const std::exception& error) { throw Py::RuntimeError(error.what()); }
        return Py::None();
    }

    Py::Object requestSectionFaces(const Py::Tuple& args) { return requestSection(args, false); }
    Py::Object requestSectionDisplay(const Py::Tuple& args) { return requestSection(args, true); }
    Py::Object requestSectionMeshDisplay(const Py::Tuple& args) { return requestSection(args, true, true); }

    Py::Object requestSection(const Py::Tuple& args, bool display, bool mesh = false)
    {
        PyObject *controller, *pythonInstances, *origin, *normal, *callback;
        double spacing = 0.0;
        const int parsed = display
            ? PyArg_ParseTuple(args.ptr(), "OOO!O!dO", &controller, &pythonInstances,
                               &Base::VectorPy::Type, &origin, &Base::VectorPy::Type, &normal,
                               &spacing, &callback)
            : PyArg_ParseTuple(args.ptr(), "OOO!O!O", &controller, &pythonInstances,
                               &Base::VectorPy::Type, &origin, &Base::VectorPy::Type, &normal, &callback);
        if (!parsed) {
            throw Py::Exception();
        }
        auto* target = sectionController(controller);
        if (!PyCallable_Check(callback)) { throw Py::TypeError("callback must be callable"); }
        std::vector<SectionInstance> instances;
        Py::Sequence sequence(pythonInstances);
        instances.reserve(sequence.size());
        for (auto iterator = sequence.begin(); iterator != sequence.end(); ++iterator) {
            Py::Tuple pair(*iterator);
            if (pair.size() != 2) { throw Py::TypeError("each instance requires a shape and matrix"); }
            Py::Object shape = pair[0];
            Py::Object matrix = pair[1];
            if (mesh) {
                if (!PyObject_TypeCheck(matrix.ptr(), &Base::MatrixPy::Type)) {
                    throw Py::TypeError("each mesh instance requires a Base matrix");
                }
                using MeshOwner = std::shared_ptr<const Part::RenderMesh>;
                auto* snapshot = static_cast<MeshOwner*>(PyCapsule_GetPointer(shape.ptr(), "PartGui.RenderMesh"));
                if (!snapshot) { throw Py::Exception(); }
                instances.push_back({{}, *static_cast<Base::MatrixPy*>(matrix.ptr())->getMatrixPtr(), *snapshot});
                continue;
            }
            if (!PyObject_TypeCheck(shape.ptr(), &Part::TopoShapePy::Type)
                || !PyObject_TypeCheck(matrix.ptr(), &Base::MatrixPy::Type)) {
                throw Py::TypeError("each instance requires a Part shape and Base matrix");
            }
            instances.push_back({
                static_cast<Part::TopoShapePy*>(shape.ptr())->getTopoShapePtr()->getShape(),
                *static_cast<Base::MatrixPy*>(matrix.ptr())->getMatrixPtr(),
                {}
            });
        }
        struct OwnerCallback
        {
            PyObject* object;
            bool display;
            OwnerCallback(PyObject* object, bool display) : object(object), display(display) { Py_INCREF(object); }
            ~OwnerCallback() { Base::PyGILStateLocker lock; Py_DECREF(object); }
            void deliver(SectionFaceResult result)
            {
                Base::PyGILStateLocker lock;
                try {
                    Py::Object payload;
                    if (display) {
                        auto geometry = std::make_unique<GeometryOwner>(std::move(result.geometry));
                        auto* capsule = PyCapsule_New(geometry.get(), "PartGui.SectionDisplayGeometry", [](PyObject* object) {
                            delete static_cast<GeometryOwner*>(PyCapsule_GetPointer(object, "PartGui.SectionDisplayGeometry"));
                        });
                        if (!capsule) { throw Py::Exception(); }
                        geometry.release();
                        payload = Py::asObject(capsule);
                    }
                    else {
                        Py::Tuple faces(result.faces.size());
                        for (std::size_t index = 0; index < result.faces.size(); ++index) {
                            faces.setItem(index, Py::asObject(new Part::TopoShapePy(
                                new Part::TopoShape(std::move(result.faces[index])))));
                        }
                        payload = faces;
                    }
                    Py::Tuple args(2);
                    args.setItem(0, payload);
                    args.setItem(1, Py::String(result.error));
                    Py::Callable(object).apply(args);
                }
                catch (Py::Exception&) {
                    PyErr_Print();
                }
            }
        };
        auto retained = std::make_shared<OwnerCallback>(callback, display);
        try {
            auto completion = [retained](SectionFaceResult result) { retained->deliver(std::move(result)); };
            const auto originValue = *static_cast<Base::VectorPy*>(origin)->getVectorPtr();
            const auto normalValue = *static_cast<Base::VectorPy*>(normal)->getVectorPtr();
            if (display) {
                target->requestDisplay(std::move(instances), originValue, normalValue, spacing, std::move(completion));
            }
            else {
                target->request(std::move(instances), originValue, normalValue, std::move(completion));
            }
        }
        catch (const std::exception& error) { throw Py::RuntimeError(error.what()); }
        return Py::None();
    }

    Py::Object isModelingObjectActive(const Py::Tuple& args)
    {
        PyObject* pythonObject = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O", &pythonObject)) {
            throw Py::Exception();
        }
        if (!PyObject_TypeCheck(pythonObject, &App::DocumentObjectPy::Type)) {
            throw Py::TypeError("object must be a document object");
        }
        auto* object =
            static_cast<App::DocumentObjectPy*>(pythonObject)->getDocumentObjectPtr();
        return Py::Boolean(PartGui::isModelingObjectActive(object));
    }

    Py::Object canStartRetainedModelingTask(const Py::Tuple& args)
    {
        if (!PyArg_ParseTuple(args.ptr(), "")) {
            throw Py::Exception();
        }
        auto* guiDocument = Gui::Application::Instance->activeDocument();
        return Py::Boolean(
            PartGui::canStartRetainedModelingTask(
                guiDocument ? guiDocument->getDocument() : nullptr
            )
        );
    }

    Py::Object resolveModelingObject(const Py::Tuple& args)
    {
        PyObject* pythonObject = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O", &pythonObject)) {
            throw Py::Exception();
        }
        if (!PyObject_TypeCheck(pythonObject, &App::DocumentObjectPy::Type)) {
            throw Py::TypeError("object must be a document object");
        }
        auto* object =
            static_cast<App::DocumentObjectPy*>(pythonObject)->getDocumentObjectPtr();
        auto* resolved = PartGui::resolveModelingObject(object);
        return resolved ? Py::asObject(resolved->getPyObject()) : Py::None();
    }

    Py::Object findModelingBody(const Py::Tuple& args)
    {
        PyObject* pythonObject = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O", &pythonObject)) {
            throw Py::Exception();
        }
        if (!PyObject_TypeCheck(pythonObject, &App::DocumentObjectPy::Type)) {
            throw Py::TypeError("object must be a document object");
        }
        auto* object =
            static_cast<App::DocumentObjectPy*>(pythonObject)->getDocumentObjectPtr();
        auto* body = PartGui::findModelingBody(object);
        return body ? Py::asObject(body->getPyObject()) : Py::None();
    }

    Py::Object resolveModelingObjectForBody(const Py::Tuple& args)
    {
        PyObject* pythonObject = nullptr;
        PyObject* pythonBody = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "OO", &pythonObject, &pythonBody)) {
            throw Py::Exception();
        }
        if (!PyObject_TypeCheck(pythonObject, &App::DocumentObjectPy::Type)
            || !PyObject_TypeCheck(pythonBody, &App::DocumentObjectPy::Type)) {
            throw Py::TypeError("object and body must be document objects");
        }
        auto* object =
            static_cast<App::DocumentObjectPy*>(pythonObject)->getDocumentObjectPtr();
        auto* bodyObject =
            static_cast<App::DocumentObjectPy*>(pythonBody)->getDocumentObjectPtr();
        auto* body = freecad_cast<Part::BodyBase*>(bodyObject);
        if (!body) {
            throw Py::TypeError("body must derive from Part::BodyBase");
        }
        auto* resolved = PartGui::resolveModelingObjectForBody(object, body);
        return resolved ? Py::asObject(resolved->getPyObject()) : Py::None();
    }

    Py::Object resolveModelingPresentationObject(const Py::Tuple& args)
    {
        PyObject* pythonObject = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O", &pythonObject)) {
            throw Py::Exception();
        }
        if (!PyObject_TypeCheck(pythonObject, &App::DocumentObjectPy::Type)) {
            throw Py::TypeError("object must be a document object");
        }
        auto* object =
            static_cast<App::DocumentObjectPy*>(pythonObject)->getDocumentObjectPtr();
        auto* resolved =
            PartGui::resolveModelingPresentationObject(object);
        return resolved ? Py::asObject(resolved->getPyObject()) : Py::None();
    }

    Py::Object setModelingReplacedInputs(const Py::Tuple& args)
    {
        PyObject* pythonResult = nullptr;
        PyObject* pythonInputs = nullptr;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "OO",
                &pythonResult,
                &pythonInputs
            )) {
            throw Py::Exception();
        }
        if (!PyObject_TypeCheck(
                pythonResult,
                &App::DocumentObjectPy::Type
            )) {
            throw Py::TypeError("result must be a document object");
        }

        auto* result =
            static_cast<App::DocumentObjectPy*>(pythonResult)
                ->getDocumentObjectPtr();
        std::vector<App::DocumentObject*> inputs;
        Py::Sequence sequence(pythonInputs);
        inputs.reserve(sequence.size());
        for (auto iterator = sequence.begin();
             iterator != sequence.end();
             ++iterator) {
            PyObject* item = (*iterator).ptr();
            if (!PyObject_TypeCheck(
                    item,
                    &App::DocumentObjectPy::Type
                )) {
                throw Py::TypeError(
                    "every replacement input must be a document object"
                );
            }
            inputs.push_back(
                static_cast<App::DocumentObjectPy*>(item)
                    ->getDocumentObjectPtr()
            );
        }
        return Py::Boolean(
            PartGui::setModelingReplacedInputs(*result, inputs)
        );
    }

    Py::Object publishDesignDefinitionBlock(const Py::Tuple& args)
    {
        PyObject* pythonOutputs = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O", &pythonOutputs)) {
            throw Py::Exception();
        }

        std::vector<App::DocumentObject*> outputs;
        Py::Sequence sequence(pythonOutputs);
        outputs.reserve(sequence.size());
        for (auto iterator = sequence.begin();
             iterator != sequence.end();
             ++iterator) {
            PyObject* item = (*iterator).ptr();
            if (!PyObject_TypeCheck(
                    item,
                    &App::DocumentObjectPy::Type
                )) {
                throw Py::TypeError(
                    "every Design definition output must be a document "
                    "object"
                );
            }
            outputs.push_back(
                static_cast<App::DocumentObjectPy*>(item)
                    ->getDocumentObjectPtr()
            );
        }
        try {
            PartGui::publishModelingDesignDefinitionBlock(outputs);
        }
        catch (const Base::Exception& error) {
            throw Py::RuntimeError(error.what());
        }
        return Py::None();
    }
};

PyObject* initModule()
{
    return Base::Interpreter().addModule(new Module);
}

}  // namespace PartGui

PyMOD_INIT_FUNC(PartGui)
{
    if (!Gui::Application::Instance) {
        PyErr_SetString(PyExc_ImportError, "Cannot load Gui module in console application.");
        PyMOD_Return(nullptr);
    }

    // load needed modules
    try {
        Base::Interpreter().runString("import Part");
        Base::Interpreter().runString("import MatGui");
    }
    catch (const Base::Exception& e) {
        PyErr_SetString(PyExc_ImportError, e.what());
        PyMOD_Return(nullptr);
    }

    PyObject* partGuiModule = PartGui::initModule();

    Base::Console().log("Loading GUI of Part module… done\n");

    Gui::BitmapFactory().addPath(QStringLiteral(":/icons/booleans"));
    Gui::BitmapFactory().addPath(QStringLiteral(":/icons/create"));
    Gui::BitmapFactory().addPath(QStringLiteral(":/icons/parametric"));
    Gui::BitmapFactory().addPath(QStringLiteral(":/icons/tools"));

    // clang-format off
    static struct PyModuleDef pAttachEngineTextsModuleDef = {
        PyModuleDef_HEAD_INIT,
        "AttachEngineResources",
        "AttachEngineResources",
        -1,
        AttacherGui::AttacherGuiPy::Methods,
        nullptr, nullptr, nullptr, nullptr
    };
    // clang-format on

    PyObject* pAttachEngineTextsModule = PyModule_Create(&pAttachEngineTextsModuleDef);

    Py_INCREF(pAttachEngineTextsModule);
    PyModule_AddObject(partGuiModule, "AttachEngineResources", pAttachEngineTextsModule);

    // clang-format off
    PartGui::PropertyEnumAttacherItem               ::init();
    PartGui::SoBrepFaceSet                          ::initClass();
    PartGui::SoBrepEdgeSet                          ::initClass();
    PartGui::SoBrepPointSet                         ::initClass();
    PartGui::SoFCControlPoints                      ::initClass();
    PartGui::SoFCShape                              ::initClass();
    PartGui::SoPreviewShape                         ::initClass();
    PartGui::ViewProviderAttachExtension            ::init();
    PartGui::ViewProviderAttachExtensionPython      ::init();
    PartGui::ViewProviderGridExtension              ::init();
    PartGui::ViewProviderGridExtensionPython        ::init();
    PartGui::ViewProviderPreviewExtension           ::init();
    PartGui::ViewProviderPreviewExtensionPython     ::init();
    PartGui::ViewProviderSplineExtension            ::init();
    PartGui::ViewProviderSplineExtensionPython      ::init();
    PartGui::ViewProviderLine                       ::init();
    PartGui::ViewProviderPlane                      ::init();
    PartGui::ViewProviderPoint                      ::init();
    PartGui::ViewProviderLCS                        ::init();
    PartGui::ViewProviderPartExt                    ::init();
    PartGui::ViewProviderPart                       ::init();
    PartGui::ViewProviderPrimitive                  ::init();
    PartGui::ViewProviderEllipsoid                  ::init();
    PartGui::ViewProviderPython                     ::init();
    PartGui::ViewProviderBox                        ::init();
    PartGui::ViewProviderPrism                      ::init();
    PartGui::ViewProviderRegularPolygon             ::init();
    PartGui::ViewProviderWedge                      ::init();
    PartGui::ViewProviderImport                     ::init();
    PartGui::ViewProviderCurveNet                   ::init();
    PartGui::ViewProviderExtrusion                  ::init();
    PartGui::ViewProviderScale                      ::init();
    PartGui::ViewProvider2DObject                   ::init();
    PartGui::ViewProvider2DObjectPython             ::init();
    PartGui::ViewProvider2DObjectGrid               ::init();
    PartGui::ViewProviderMirror                     ::init();
    PartGui::ViewProviderFillet                     ::init();
    PartGui::ViewProviderChamfer                    ::init();
    PartGui::ViewProviderRevolution                 ::init();
    PartGui::ViewProviderLoft                       ::init();
    PartGui::ViewProviderSweep                      ::init();
    PartGui::ViewProviderOffset                     ::init();
    PartGui::ViewProviderOffset2D                   ::init();
    PartGui::ViewProviderThickness                  ::init();
    PartGui::ViewProviderRefine                     ::init();
    PartGui::ViewProviderReverse                    ::init();
    PartGui::ViewProviderCustom                     ::init();
    PartGui::ViewProviderCustomPython               ::init();
    PartGui::ViewProviderBoolean                    ::init();
    PartGui::ViewProviderMultiFuse                  ::init();
    PartGui::ViewProviderMultiCommon                ::init();
    PartGui::ViewProviderCompound                   ::init();
    PartGui::ViewProviderSpline                     ::init();
    PartGui::ViewProviderCircleParametric           ::init();
    PartGui::ViewProviderLineParametric             ::init();
    PartGui::ViewProviderPointParametric            ::init();
    PartGui::ViewProviderEllipseParametric          ::init();
    PartGui::ViewProviderHelixParametric            ::init();
    PartGui::ViewProviderSpiralParametric           ::init();
    PartGui::ViewProviderPlaneParametric            ::init();
    PartGui::ViewProviderSphereParametric           ::init();
    PartGui::ViewProviderCylinderParametric         ::init();
    PartGui::ViewProviderConeParametric             ::init();
    PartGui::ViewProviderTorusParametric            ::init();
    PartGui::ViewProviderRuledSurface               ::init();
    PartGui::ViewProviderFace                       ::init();
    PartGui::ViewProviderProjectOnSurface           ::init();

    auto manip = std::make_shared<PartGui::WorkbenchManipulator>();
    Gui::WorkbenchManipulator::installManipulator(manip);

    Base::registerServiceImplementation<Part::PreviewUpdateScheduler>(new PartGui::QtPreviewUpdateScheduler);

    // instantiating the commands
    CreatePartCommands();
    CreateSimplePartCommands();
    CreateParamPartCommands();
    CreatePartSelectCommands();
    try {
        const char* cmd = "__import__('AttachmentEditor.Commands').Commands";
        Py::Object ae = Base::Interpreter().runStringObject(cmd);
        Py::Module(partGuiModule).setAttr(std::string("AttachmentEditor"), ae);
    }
    catch (Base::PyException& err) {
        err.reportException();
    }

    // register preferences pages
    Gui::Dialog::DlgPreferencesImp::setGroupData("Part/Part Design", "Part design", QObject::tr("Part and Part Design workbench"));
    (void)new Gui::PrefPageProducer<PartGui::DlgSettingsGeneral>(QT_TRANSLATE_NOOP("QObject", "Part/Part Design"));
    (void)new Gui::PrefPageProducer<PartGui::DlgSettings3DViewPart>(QT_TRANSLATE_NOOP("QObject", "Part/Part Design"));
    (void)new Gui::PrefPageProducer<PartGui::DlgSettingsObjectColor>(QT_TRANSLATE_NOOP("QObject", "Part/Part Design"));
    (void)new Gui::PrefPageProducer<PartGui::DlgImportExportIges>(QT_TRANSLATE_NOOP("QObject", "Import-Export"));
    (void)new Gui::PrefPageProducer<PartGui::DlgImportExportStep>(QT_TRANSLATE_NOOP("QObject", "Import-Export"));
    Gui::ViewProviderBuilder::add(Part::PropertyPartShape::getClassTypeId(),
                                  PartGui::ViewProviderPart::getClassTypeId());
    // clang-format on

    // add resources and reloads the translators
    loadPartResource();

    PyMOD_Return(partGuiModule);
}
