// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2024 Florian Foinant-Willig <ffw@2f2v.fr>               *
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

#pragma once

#include <App/DocumentObject.h>
#include <App/DocumentObjectExtension.h>
#include <App/ExtensionPython.h>

namespace App
{
class SuppressibleExtensionPy;

class AppExport SuppressibleExtension: public DocumentObjectExtension
{
    EXTENSION_PROPERTY_HEADER_WITH_OVERRIDE(App::SuppressibleExtension);
    using inherited = DocumentObjectExtension;

public:
    /// Constructor
    SuppressibleExtension();
    ~SuppressibleExtension() override;

    PyObject* getExtensionPyObject() override;

    /**
     * Declare that the owner computes a bypass result while suppressed.
     *
     * The document timeline normally hides future operations. A parametric
     * filter may opt in when its suppressed output is deliberately the
     * unchanged upstream result; the last normally-visible filter can then
     * keep rendering the correct pre-marker state without exposing every
     * intermediate object.
     */
    void setTimelineResultVisibleWhenSuppressed(bool visible) noexcept
    {
        timelineResultVisibleWhenSuppressed = visible;
    }
    [[nodiscard]] bool isTimelineResultVisibleWhenSuppressed() const noexcept
    {
        return timelineResultVisibleWhenSuppressed;
    }

    /// Properties
    PropertyBool Suppressed;

private:
    bool timelineResultVisibleWhenSuppressed {false};
};

template<typename ExtensionT>
class SuppressibleExtensionPythonT: public ExtensionT
{

public:
    SuppressibleExtensionPythonT() = default;
    ~SuppressibleExtensionPythonT() override = default;
};

using SuppressibleExtensionPython =
    ExtensionPythonT<SuppressibleExtensionPythonT<SuppressibleExtension>>;

}  // namespace App
