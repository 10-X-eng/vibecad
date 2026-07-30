// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <string>

#include <App/Document.h>
#include <App/DocumentObject.h>
#include <App/DocumentObjectPy.h>
#include <Base/Exception.h>
#include <Base/Interpreter.h>
#include <Mod/Part/App/PartFeature.h>

namespace PartGui::TaskResultValidation
{

inline App::DocumentObject*
requireExactPartResult(App::Document& document, const Py::Object& value)
{
    Base::PyGILStateLocker locker;
    if (value.isNone()
        || !PyObject_TypeCheck(value.ptr(), &App::DocumentObjectPy::Type)) {
        throw Base::RuntimeError(
            "Operation did not return a Part document object"
        );
    }

    auto* result =
        static_cast<App::DocumentObjectPy*>(value.ptr())
            ->getDocumentObjectPtr();
    if (!result || result->getDocument() != &document
        || !result->getNameInDocument()
        || !document.containsObject(result)
        || !result->isDerivedFrom<Part::Feature>()) {
        throw Base::RuntimeError(
            "Operation returned an invalid Part result identity"
        );
    }
    return result;
}

inline App::DocumentObject*
requirePythonPartResult(App::Document& document, const char* expression)
{
    Base::PyGILStateLocker locker;
    const Py::Object value =
        Base::Interpreter().runStringObject(expression);
    return requireExactPartResult(document, value);
}

inline void validatePartResult(App::DocumentObject* result)
{
    if (!result || !result->isAttachedToDocument()) {
        throw Base::RuntimeError("Operation result is no longer in the document");
    }
    if (!result->isValid()) {
        const char* status = result->getStatusString();
        throw Base::RuntimeError(
            status && *status ? status : "Operation result is invalid"
        );
    }

    const auto shape =
        Part::Feature::getTopoShape(result, Part::ShapeOption::NoFlag);
    if (shape.isNull() || shape.getShape().IsNull()) {
        throw Base::RuntimeError(
            std::string(result->getFullLabel()) + " produced no shape"
        );
    }
    if (!shape.isValid()) {
        throw Base::RuntimeError(
            std::string(result->getFullLabel()) + " produced an invalid shape"
        );
    }
}

}  // namespace PartGui::TaskResultValidation
