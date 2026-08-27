// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2026 VibeCAD contributors                               *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public           *
 *   License as published by the Free Software Foundation; either          *
 *   version 2 of the License, or (at your option) any later version.      *
 *                                                                         *
 ***************************************************************************/

#pragma once

#include <App/PropertyLinks.h>
#include <App/PropertyStandard.h>
#include <App/PropertyUnits.h>
#include <App/SuppressibleExtension.h>
#include <Mod/Mesh/App/MeshFeature.h>

#include <Mod/MeshPart/MeshPartGlobal.h>


namespace MeshPart
{

/**
 * A placement-aware, parametric boolean between two closed mesh solids.
 *
 * Mesh facets are converted to sewn OCC shells, promoted to solids, evaluated
 * with Open CASCADE's boolean algorithms, and tessellated back to a mesh.
 * Keeping source links and tessellation settings on the object makes native
 * Mesh ribbon booleans editable and recomputable.
 */
class MeshPartExport Boolean: public Mesh::Feature
{
    PROPERTY_HEADER_WITH_OVERRIDE(MeshPart::Boolean);

public:
    Boolean();

    App::PropertyLink Source1;
    App::PropertyLink Source2;
    App::PropertyEnumeration Operation;
    App::PropertyLength LinearDeflection;
    App::PropertyFloatConstraint AngularDeflection;
    App::PropertyBool Relative;
    App::PropertyBool UpdateFromSource;

    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;

private:
    [[nodiscard]] bool isSuppressed() const;

    static const char* OperationEnums[];
    App::SuppressibleExtension suppressibleExt;
};

}  // namespace MeshPart
