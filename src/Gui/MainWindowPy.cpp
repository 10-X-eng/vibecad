/***************************************************************************
 *   Copyright (c) 2021 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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


#include <list>

#include <QAction>
#include <QDockWidget>

#include <Base/TypePy.h>

#include "DocumentPy.h"
#include "MainWindowPy.h"
#include "MainWindow.h"
#include "DockWindowManager.h"
#include "MDIView.h"
#include "MDIViewPy.h"
#include "MDIViewPyWrap.h"
#include "PythonWrapper.h"


using namespace Gui;


void MainWindowPy::init_type()
{
    behaviors().name("MainWindowPy");
    behaviors().doc("Python binding class for the MainWindow class");
    // you must have overwritten the virtual functions
    behaviors().supportRepr();
    behaviors().supportGetattr();
    behaviors().supportSetattr();
    behaviors().set_tp_new(extension_object_new);

    add_varargs_method("getWindows", &MainWindowPy::getWindows, "getWindows()");
    add_varargs_method("getWindowsOfType", &MainWindowPy::getWindowsOfType, "getWindowsOfType(typeid)");
    add_varargs_method("setActiveWindow", &MainWindowPy::setActiveWindow, "setActiveWindow(MDIView)");
    add_varargs_method("getActiveWindow", &MainWindowPy::getActiveWindow, "getActiveWindow()");
    add_varargs_method("addWindow", &MainWindowPy::addWindow, "addWindow(MDIView)");
    add_varargs_method("removeWindow", &MainWindowPy::removeWindow, "removeWindow(MDIView)");
    add_varargs_method("showHint", &MainWindowPy::showHint, "showHint(hint)");
    add_varargs_method("hideHint", &MainWindowPy::hideHint, "hideHint()");
    add_keyword_method(
        "addStatusBarItem",
        &MainWindowPy::addStatusBarItem,
        "addStatusBarItem(widget, id, title='', slot='Right', order=0, "
        "persistentVisibility=True, stretch=0)\n"
        "Registers a widget in the status bar. MainWindow owns its "
        "placement, ordering, visibility persistence and context-menu entry."
    );
    add_varargs_method(
        "removeStatusBarItem",
        &MainWindowPy::removeStatusBarItem,
        "removeStatusBarItem(id)"
    );
    add_keyword_method(
        "addDockWindow",
        &MainWindowPy::addDockWindow,
        "addDockWindow(widget, name, area='right')\n"
        "Registers a widget as a first-class dock window through the "
        "DockWindowManager. The dock gets the native overlay title bar, "
        "overlay-mode eligibility, visibility persistence and a "
        "View->Panels entry. Returns the QDockWidget container."
    );
    add_keyword_method(
        "registerDockWindow",
        &MainWindowPy::registerDockWindow,
        "registerDockWindow(widget, name)\n"
        "Registers dock content without creating or showing a QDockWidget. "
        "The active workbench's DockWindowManager setup owns creation, "
        "visibility and placement."
    );
    add_varargs_method(
        "removeDockWindow",
        &MainWindowPy::removeDockWindow,
        "removeDockWindow(name)\n"
        "Removes the dock window with the given name; the embedded "
        "widget is not destroyed."
    );
    behaviors().readyType();
}

PyObject* MainWindowPy::extension_object_new(struct _typeobject* /*type*/, PyObject* /*args*/, PyObject* /*kwds*/)
{
    return new MainWindowPy(nullptr);
}

Py::Object MainWindowPy::type()
{
    return Py::Object(reinterpret_cast<PyObject*>(behaviors().type_object()));
}

Py::ExtensionObject<MainWindowPy> MainWindowPy::create(MainWindow* mw)
{
    Py::Callable class_type(type());
    Py::Tuple arg;
    auto inst = Py::ExtensionObject<MainWindowPy>(class_type.apply(arg, Py::Dict()));
    inst.extensionObject()->_mw = mw;
    return inst;
}

Py::Object MainWindowPy::createWrapper(MainWindow* mw)
{
    PythonWrapper wrap;
    if (!wrap.loadCoreModule() || !wrap.loadGuiModule() || !wrap.loadWidgetsModule()) {
        throw Py::RuntimeError("Failed to load Python wrapper for Qt");
    }

    // copy attributes
    static constexpr std::initializer_list<const char*> attr = {
        "getWindows",
        "getWindowsOfType",
        "setActiveWindow",
        "getActiveWindow",
        "addWindow",
        "removeWindow",
        "showHint",
        "hideHint",
        "addStatusBarItem",
        "removeStatusBarItem",
        "addDockWindow",
        "registerDockWindow",
        "removeDockWindow",
    };

    Py::Object py = wrap.fromQWidget(mw, "QMainWindow");
    Py::ExtensionObject<MainWindowPy> inst(create(mw));
    for (const auto& it : attr) {
        py.setAttr(it, inst.getAttr(it));
    }
    return py;
}

MainWindowPy::MainWindowPy(MainWindow* mw)
    : _mw(mw)
{}

MainWindowPy::~MainWindowPy()
{
    // in case the class is instantiated on the stack
    ob_refcnt = 0;
}

Py::Object MainWindowPy::repr()
{
    if (!_mw) {
        throw Py::RuntimeError("Cannot print representation of deleted object");
    }

    return Py::String("MainWindow");
}

Py::Object MainWindowPy::getWindows(const Py::Tuple& args)
{
    if (!PyArg_ParseTuple(args.ptr(), "")) {
        throw Py::Exception();
    }

    Py::List mdis;
    if (_mw) {
        QList<QWidget*> windows = _mw->windows();
        for (auto it : windows) {
            auto view = dynamic_cast<MDIView*>(it);
            if (view) {
                mdis.append(Py::asObject(view->getPyObject()));
            }
        }
    }

    return mdis;
}

Py::Object MainWindowPy::getWindowsOfType(const Py::Tuple& args)
{
    PyObject* t;
    if (!PyArg_ParseTuple(args.ptr(), "O!", &Base::TypePy::Type, &t)) {
        throw Py::Exception();
    }

    Base::Type typeId = *static_cast<Base::TypePy*>(t)->getBaseTypePtr();

    Py::List mdis;
    if (_mw) {
        QList<QWidget*> windows = _mw->windows();
        for (auto it : windows) {
            auto view = dynamic_cast<MDIView*>(it);
            if (view && view->isDerivedFrom(typeId)) {
                mdis.append(Py::asObject(view->getPyObject()));
            }
        }
    }

    return mdis;
}

Py::Object MainWindowPy::setActiveWindow(const Py::Tuple& args)
{
    Py::ExtensionObject<MDIViewPy> mdi(args[0].callMemberFunction("cast_to_base"));
    if (_mw) {
        _mw->setActiveWindow(mdi.extensionObject()->getMDIViewPtr());
    }

    return Py::None();
}

Py::Object MainWindowPy::getActiveWindow(const Py::Tuple& args)
{
    if (!PyArg_ParseTuple(args.ptr(), "")) {
        throw Py::Exception();
    }

    if (_mw) {
        MDIView* mdi = _mw->activeWindow();
        if (mdi) {
            return Py::asObject(mdi->getPyObject());
        }
    }
    return Py::None();
}

Py::Object MainWindowPy::addWindow(const Py::Tuple& args)
{
    PyObject* obj;
    if (!PyArg_ParseTuple(args.ptr(), "O", &obj)) {
        throw Py::Exception();
    }

    if (_mw) {
        Py::Object py(obj);
        Gui::Document* document {nullptr};
        // Check if the py object has a reference to a Gui document
        if (py.hasAttr("document")) {
            Py::Object attr(py.getAttr("document"));
            if (PyObject_TypeCheck(attr.ptr(), &DocumentPy::Type)) {
                document = static_cast<DocumentPy*>(attr.ptr())->getDocumentPtr();
            }
        }

        MDIViewPyWrap* mdi = new MDIViewPyWrap(py, document);
        _mw->addWindow(mdi);
        return Py::asObject(mdi->getPyObject());
    }
    return Py::None();
}

Py::Object MainWindowPy::removeWindow(const Py::Tuple& args)
{
    Py::ExtensionObject<MDIViewPy> mdi(args[0].callMemberFunction("cast_to_base"));
    if (_mw) {
        _mw->removeWindow(mdi.extensionObject()->getMDIViewPtr());
    }
    return Py::None();
}

Py::Object MainWindowPy::showHint(const Py::Tuple& args)
{
    static auto userInputFromPyObject = [](const Py::Object& object) -> InputHint::UserInput {
        Py::Long value(object.getAttr("value"));

        return static_cast<InputHint::UserInput>(value.as_long());
    };

    static auto inputSequenceFromPyObject = [](const Py::Object& sequence) -> InputHint::InputSequence {
        if (sequence.isTuple()) {
            Py::Tuple pyInputs(sequence);

            std::list<InputHint::UserInput> inputs;
            for (auto pyInput : pyInputs) {
                inputs.push_back(userInputFromPyObject(pyInput));
            }

            return InputHint::InputSequence(inputs);
        }

        return userInputFromPyObject(sequence);
    };

    static auto hintFromPyObject = [](const Py::Object& object) -> InputHint {
        Py::List pySequences = object.getAttr("sequences");

        std::list<InputHint::InputSequence> sequences;
        for (auto pySequence : pySequences) {
            sequences.push_back(inputSequenceFromPyObject(pySequence));
        }

        return InputHint {
            .message = QString::fromStdString(object.getAttr("message").as_string()),
            .sequences = sequences
        };
    };

    std::list<InputHint> hints;
    for (auto arg : args) {
        hints.push_back(hintFromPyObject(arg));
    }

    _mw->showHints(hints);

    return Py::None();
}

Py::Object MainWindowPy::hideHint(const Py::Tuple&)
{
    _mw->hideHints();

    return Py::None();
}

Py::Object MainWindowPy::addStatusBarItem(const Py::Tuple& args, const Py::Dict& kwds)
{
    PyObject* pyWidget {};
    const char* id {};
    const char* title = "";
    const char* slot = "Right";
    int order = 0;
    int persistent = 1;
    int stretch = 0;

    // Parse positional + keyword arguments.
    static char* argNames[] = {
        const_cast<char*>("widget"),
        const_cast<char*>("id"),
        const_cast<char*>("title"),
        const_cast<char*>("slot"),
        const_cast<char*>("order"),
        const_cast<char*>("persistentVisibility"),
        const_cast<char*>("stretch"),
        nullptr
    };
    if (!PyArg_ParseTupleAndKeywords(
            args.ptr(),
            kwds.ptr(),
            "Os|ssipi",
            argNames,
            &pyWidget,
            &id,
            &title,
            &slot,
            &order,
            &persistent,
            &stretch
        )) {
        throw Py::Exception();
    }

    PythonWrapper wrap;
    wrap.loadWidgetsModule();
    QWidget* widget = qobject_cast<QWidget*>(wrap.toQObject(Py::Object(pyWidget)));
    if (!widget) {
        throw Py::TypeError("addStatusBarItem: first argument must be a QWidget");
    }

    StatusBarItemSpec spec;
    spec.id = QByteArray(id);
    spec.title = QString::fromUtf8(title);
    spec.slot = (QByteArray(slot).toLower() == "left") ? StatusBarSlot::Left : StatusBarSlot::Right;
    spec.order = order;
    spec.persistentVisibility = persistent != 0;
    spec.stretch = stretch;

    if (_mw) {
        _mw->addStatusBarItem(widget, spec);
    }
    return Py::None();
}

Py::Object MainWindowPy::removeStatusBarItem(const Py::Tuple& args)
{
    const char* id {};
    if (!PyArg_ParseTuple(args.ptr(), "s", &id)) {
        throw Py::Exception();
    }
    if (_mw) {
        _mw->removeStatusBarItem(QByteArray(id));
    }
    return Py::None();
}

Py::Object MainWindowPy::addDockWindow(const Py::Tuple& args, const Py::Dict& kwds)
{
    PyObject* pyWidget {};
    const char* name {};
    const char* area = "right";

    static char* argNames[] = {
        const_cast<char*>("widget"),
        const_cast<char*>("name"),
        const_cast<char*>("area"),
        nullptr
    };
    if (!PyArg_ParseTupleAndKeywords(
            args.ptr(),
            kwds.ptr(),
            "Os|s",
            argNames,
            &pyWidget,
            &name,
            &area
        )) {
        throw Py::Exception();
    }

    PythonWrapper wrap;
    if (!wrap.loadCoreModule() || !wrap.loadGuiModule() || !wrap.loadWidgetsModule()) {
        throw Py::RuntimeError("addDockWindow: failed to load Python wrapper for Qt");
    }
    QWidget* widget = qobject_cast<QWidget*>(wrap.toQObject(Py::Object(pyWidget)));
    if (!widget) {
        throw Py::TypeError("addDockWindow: first argument must be a QWidget");
    }

    const QByteArray areaName = QByteArray(area).toLower();
    Qt::DockWidgetArea pos = Qt::AllDockWidgetAreas;
    if (areaName == "left") {
        pos = Qt::LeftDockWidgetArea;
    }
    else if (areaName == "right") {
        pos = Qt::RightDockWidgetArea;
    }
    else if (areaName == "top") {
        pos = Qt::TopDockWidgetArea;
    }
    else if (areaName == "bottom") {
        pos = Qt::BottomDockWidgetArea;
    }
    else if (areaName != "all") {
        throw Py::ValueError(
            "addDockWindow: area must be one of 'left', 'right', 'top', 'bottom' or 'all'"
        );
    }

    DockWindowManager* dwm = DockWindowManager::instance();
    if (dwm->getDockWindow(name)) {
        throw Py::ValueError("addDockWindow: a dock window with this name already exists");
    }

    dwm->registerDockWindow(name, widget);
    QDockWidget* dw = dwm->addDockWindow(name, widget, pos);
    if (!dw) {
        throw Py::RuntimeError("addDockWindow: failed to create the dock window");
    }

    // DockWindowManager reparents the widget in C++, outside PySide's view.
    // Mirror that ownership change so the Python wrapper cannot delete the
    // native panel when the caller's temporary reference goes out of scope.
    PythonWrapper::setParent(pyWidget, dw);

    // Mirror DockWindowManager::setup(): the toggle-view action data is the
    // key used for visibility persistence, and addDockWindow() hides the
    // dock by default to avoid flicker during layout restore.
    dw->toggleViewAction()->setData(QString::fromUtf8(name));
    widget->show();
    dw->show();

    return wrap.fromQWidget(dw, "QDockWidget");
}

Py::Object MainWindowPy::registerDockWindow(const Py::Tuple& args, const Py::Dict& kwds)
{
    PyObject* pyWidget {};
    const char* name {};

    static char* argNames[] = {
        const_cast<char*>("widget"),
        const_cast<char*>("name"),
        nullptr
    };
    if (!PyArg_ParseTupleAndKeywords(
            args.ptr(),
            kwds.ptr(),
            "Os",
            argNames,
            &pyWidget,
            &name
        )) {
        throw Py::Exception();
    }

    PythonWrapper wrap;
    if (!wrap.loadCoreModule() || !wrap.loadGuiModule() || !wrap.loadWidgetsModule()) {
        throw Py::RuntimeError("registerDockWindow: failed to load Python wrapper for Qt");
    }
    QWidget* widget = qobject_cast<QWidget*>(wrap.toQObject(Py::Object(pyWidget)));
    if (!widget) {
        throw Py::TypeError("registerDockWindow: first argument must be a QWidget");
    }
    if (!_mw) {
        throw Py::RuntimeError("registerDockWindow: main window is unavailable");
    }

    DockWindowManager* dwm = DockWindowManager::instance();
    if (dwm->findRegisteredDockWindow(name)) {
        throw Py::ValueError("registerDockWindow: a dock window with this name already exists");
    }
    widget->setParent(_mw);
    if (!dwm->registerDockWindow(name, widget)) {
        widget->setParent(nullptr);
        throw Py::RuntimeError("registerDockWindow: failed to register the dock content");
    }

    // Registration reparents the widget in C++, outside PySide's view. Keep
    // the wrapper owned by MainWindow until DockWindowManager reparents it to
    // the QDockWidget created during workbench setup.
    PythonWrapper::setParent(pyWidget, _mw);
    return Py::None();
}

Py::Object MainWindowPy::removeDockWindow(const Py::Tuple& args)
{
    const char* name {};
    if (!PyArg_ParseTuple(args.ptr(), "s", &name)) {
        throw Py::Exception();
    }
    DockWindowManager* dwm = DockWindowManager::instance();
    dwm->unregisterDockWindow(name);
    QWidget* widget = dwm->removeDockWindow(name);
    if (widget) {
        // detach so the caller keeps ownership of the embedded widget
        widget->setParent(nullptr);
    }
    return Py::None();
}
