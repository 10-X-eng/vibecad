// SPDX-License-Identifier: LGPL-2.1-or-later

#include <QApplication>
#include <QThread>

#include <App/PropertyPythonObject.h>
#include <Base/Exception.h>
#include <Base/Interpreter.h>

#include "ViewProviderCachedDetails.h"

using namespace PartGui;

PROPERTY_SOURCE(PartGui::ViewProviderCachedDetails, PartGui::ViewProviderCached)
PROPERTY_SOURCE_TEMPLATE(PartGui::ViewProviderCachedDetailsPython, PartGui::ViewProviderCachedDetails)

ViewProviderCachedDetails::ViewProviderCachedDetails() = default;
ViewProviderCachedDetails::~ViewProviderCachedDetails() = default;

namespace
{
void requireGuiThread()
{
    if (!qApp || QThread::currentThread() != qApp->thread()) {
        throw Base::RuntimeError("Tree details require the GUI thread");
    }
}

Py::Object pythonProxy(const Gui::ViewProvider* provider)
{
    const auto* property = dynamic_cast<const App::PropertyPythonObject*>(
        provider->getPropertyByName("Proxy"));
    return property ? property->getValue() : Py::None();
}

std::string textField(const Py::Dict& row, const char* key)
{
    if (!row.hasKey(key)) {
        return {};
    }
    return Py::String(row.getItem(key)).as_std_string();
}
}

std::vector<Gui::TreeViewDetail> ViewProviderCachedDetails::getTreeViewDetails() const
{
    requireGuiThread();
    Base::PyGILStateLocker lock;
    try {
        const auto proxy = pythonProxy(this);
        if (proxy.isNone() || !proxy.hasAttr("getTreeViewDetails")) {
            return {};
        }
        const Py::Sequence rows(Py::Callable(proxy.getAttr("getTreeViewDetails")).apply(Py::Tuple()));
        std::vector<Gui::TreeViewDetail> result;
        result.reserve(rows.size());
        for (const auto& value : rows) {
            const Py::Dict row(value);
            result.push_back({textField(row, "key"), textField(row, "label"),
                              textField(row, "secondary_text"), textField(row, "tooltip"),
                              textField(row, "icon")});
        }
        return result;
    }
    catch (Py::Exception&) {
        throw Base::PyException();
    }
}

bool ViewProviderCachedDetails::activateTreeViewDetail(const std::string& key)
{
    requireGuiThread();
    Base::PyGILStateLocker lock;
    try {
        const auto proxy = pythonProxy(this);
        if (proxy.isNone() || !proxy.hasAttr("activateTreeViewDetail")) {
            return false;
        }
        Py::Tuple args(1);
        args.setItem(0, Py::String(key));
        return static_cast<bool>(Py::Boolean(
            Py::Callable(proxy.getAttr("activateTreeViewDetail")).apply(args)));
    }
    catch (Py::Exception&) {
        throw Base::PyException();
    }
}

bool ViewProviderCachedDetails::treeViewDetailsAffectedBy(const std::string& propertyName) const
{
    requireGuiThread();
    Base::PyGILStateLocker lock;
    try {
        const auto proxy = pythonProxy(this);
        if (proxy.isNone() || !proxy.hasAttr("treeViewDetailsAffectedBy")) {
            return false;
        }
        Py::Tuple args(1);
        args.setItem(0, Py::String(propertyName));
        return static_cast<bool>(Py::Boolean(
            Py::Callable(proxy.getAttr("treeViewDetailsAffectedBy")).apply(args)));
    }
    catch (Py::Exception&) {
        // A presentation callback must not interrupt the document's property
        // change signal. Report the defect and conservatively refresh its rows.
        Base::PyException error;
        error.reportException();
        return true;
    }
}
