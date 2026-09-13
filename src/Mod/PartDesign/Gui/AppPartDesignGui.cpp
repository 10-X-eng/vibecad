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


#include <CXX/Extensions.hxx>
#include <CXX/Objects.hxx>

#include <Base/Console.h>
#include <Base/Interpreter.h>
#include <Base/PyObjectBase.h>
#include <App/DocumentObjectPy.h>
#include <Gui/Application.h>
#include <Gui/Language/Translator.h>
#include <Mod/PartDesign/App/Body.h>

#include "Workbench.h"
#include "ModelingContext.h"
#include "ViewProviderBase.h"
#include "ViewProviderBody.h"
#include "ViewProviderBoolean.h"
#include "ViewProviderDesignOperation.h"
#include "ViewProviderDesignScriptOperation.h"
#include "ViewProviderChamfer.h"
#include "ViewProviderDatumCS.h"
#include "ViewProviderDatumLine.h"
#include "ViewProviderDatumPlane.h"
#include "ViewProviderDatumPoint.h"
#include "ViewProviderDraft.h"
#include "ViewProviderDressUp.h"
#include "ViewProviderFillet.h"
#include "ViewProviderGroove.h"
#include "ViewProviderHelix.h"
#include "ViewProviderHole.h"
#include "ViewProviderLinearPattern.h"
#include "ViewProviderLoft.h"
#include "ViewProviderMirrored.h"
#include "ViewProviderMultiTransform.h"
#include "ViewProviderPad.h"
#include "ViewProviderPipe.h"
#include "ViewProviderPocket.h"
#include "ViewProviderPolarPattern.h"
#include "ViewProviderPrimitive.h"
#include "ViewProviderRevolution.h"
#include "ViewProviderScaled.h"
#include "ViewProviderShapeBinder.h"
#include "ViewProviderSketchBased.h"
#include "ViewProviderThickness.h"
#include "ViewProviderTransformed.h"


// use a different name to CreateCommand()
void CreatePartDesignCommands();
void CreatePartDesignBodyCommands();
void CreatePartDesignPrimitiveCommands();

void loadPartDesignResource()
{
    // add resources and reloads the translators
    Q_INIT_RESOURCE(PartDesign);
    Q_INIT_RESOURCE(PartDesign_translation);
    Gui::Translator::instance()->refresh();
}

namespace PartDesignGui
{
class Module: public Py::ExtensionModule<Module>
{
public:
    Module()
        : Py::ExtensionModule<Module>("PartDesignGui")
    {
        add_varargs_method(
            "adoptPartResult",
            &Module::adoptPartResult,
            "adoptPartResult(result, body=None) -> Body or None\n\n"
            "Atomically place a completed ordinary Part result and its unowned "
            "dependencies in a Body. Existing group ownership is never changed."
        );
        initialize("This module is the PartDesignGui module.");  // register with Python
    }

private:
    Py::Object adoptPartResult(const Py::Tuple& args)
    {
        PyObject* resultObject = nullptr;
        PyObject* bodyObject = Py_None;
        if (!PyArg_ParseTuple(args.ptr(), "O|O", &resultObject, &bodyObject)) {
            throw Py::Exception();
        }
        if (!PyObject_TypeCheck(resultObject, &App::DocumentObjectPy::Type)) {
            throw Py::TypeError("result must be a document object");
        }
        auto* result = static_cast<App::DocumentObjectPy*>(resultObject)->getDocumentObjectPtr();

        PartDesign::Body* body = nullptr;
        if (bodyObject != Py_None) {
            if (!PyObject_TypeCheck(bodyObject, &App::DocumentObjectPy::Type)) {
                throw Py::TypeError("body must be a PartDesign Body or None");
            }
            auto* candidate
                = static_cast<App::DocumentObjectPy*>(bodyObject)->getDocumentObjectPtr();
            body = freecad_cast<PartDesign::Body*>(candidate);
            if (!body) {
                throw Py::TypeError("body must be a PartDesign Body or None");
            }
        }

        auto* adopted = ModelingContext::instance().adoptPartResult(result, body);
        return adopted ? Py::asObject(adopted->getPyObject()) : Py::None();
    }
};

PyObject* initModule()
{
    return Base::Interpreter().addModule(new Module);
}

}  // namespace PartDesignGui


/* Python entry */
PyMOD_INIT_FUNC(PartDesignGui)
{
    if (!Gui::Application::Instance) {
        PyErr_SetString(PyExc_ImportError, "Cannot load Gui module in console application.");
        PyMOD_Return(nullptr);
    }

    try {
        Base::Interpreter().runString("import PartGui");
        Base::Interpreter().runString("import SketcherGui");
    }
    catch (const Base::Exception& e) {
        PyErr_SetString(PyExc_ImportError, e.what());
        PyMOD_Return(nullptr);
    }

    PyObject* mod = PartDesignGui::initModule();
    Base::Console().log("Loading GUI of Part Design module… done\n");

    // instantiating the commands
    CreatePartDesignCommands();
    CreatePartDesignBodyCommands();
    CreatePartDesignPrimitiveCommands();
    PartDesignGui::ModelingContext::instance();

    // clang-format off
    PartDesignGui::Workbench                 ::init();
    PartDesignGui::ViewProvider              ::init();
    PartDesignGui::ViewProviderPython        ::init();
    PartDesignGui::ViewProviderBody          ::init();
    PartDesignGui::ViewProviderSketchBased   ::init();
    PartDesignGui::ViewProviderExtrude       ::init();
    PartDesignGui::ViewProviderPocket        ::init();
    PartDesignGui::ViewProviderHole          ::init();
    PartDesignGui::ViewProviderPad           ::init();
    PartDesignGui::ViewProviderRevolution    ::init();
    PartDesignGui::ViewProviderDressUp       ::init();
    PartDesignGui::ViewProviderGroove        ::init();
    PartDesignGui::ViewProviderChamfer       ::init();
    PartDesignGui::ViewProviderFillet        ::init();
    PartDesignGui::ViewProviderDraft         ::init();
    PartDesignGui::ViewProviderThickness     ::init();
    PartDesignGui::ViewProviderTransformed   ::init();
    PartDesignGui::ViewProviderMirrored      ::init();
    PartDesignGui::ViewProviderLinearPattern ::init();
    PartDesignGui::ViewProviderPolarPattern  ::init();
    PartDesignGui::ViewProviderScaled        ::init();
    PartDesignGui::ViewProviderMultiTransform::init();
    PartDesignGui::ViewProviderDatum         ::init();
    PartDesignGui::ViewProviderDatumPoint    ::init();
    PartDesignGui::ViewProviderDatumLine     ::init();
    PartDesignGui::ViewProviderDatumPlane    ::init();
    PartDesignGui::ViewProviderDatumCoordinateSystem::init();
    PartDesignGui::ViewProviderShapeBinder   ::init();
    PartDesignGui::ViewProviderSubShapeBinder::init();
    PartDesignGui::ViewProviderSubShapeBinderPython::init();
    PartDesignGui::ViewProviderBoolean       ::init();
    PartDesignGui::ViewProviderDesignOperation::init();
    PartDesignGui::ViewProviderDesignScriptOperation::init();
    PartDesignGui::ViewProviderPrimitive     ::init();
    PartDesignGui::ViewProviderPipe          ::init();
    PartDesignGui::ViewProviderLoft          ::init();
    PartDesignGui::ViewProviderHelix         ::init();
    PartDesignGui::ViewProviderBase          ::init();
    // clang-format on

    // add resources and reloads the translators
    loadPartDesignResource();

    PyMOD_Return(mod);
}
