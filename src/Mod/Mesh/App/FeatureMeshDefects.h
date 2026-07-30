// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2006 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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

#include <App/PropertyLinks.h>
#include <App/PropertyStandard.h>
#include <App/SuppressibleExtension.h>

#include "MeshFeature.h"


namespace Mesh
{

/**
 * The FixDefects class fixes defects in the attached mesh kernel.
 * @author Werner Mayer
 */
class MeshExport FixDefects: public Mesh::Feature
{
    PROPERTY_HEADER_WITH_OVERRIDE(Mesh::FixDefects);

public:
    /// Constructor
    FixDefects();

    /** @name Properties */
    //@{
    App::PropertyLink Source;
    App::PropertyFloat Epsilon;
    //@}

    /** @name methods override Feature */
    //@{
    /// recalculate the Feature
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    //@}

    /// returns the type name of the ViewProvider
    //  virtual const char* getViewProviderName(void) const {return "MeshGui::ViewProviderDefects";}

protected:
    App::DocumentObjectExecReturn* loadSourceMesh(MeshObject& mesh) const;
    bool isSuppressed() const;

private:
    App::SuppressibleExtension suppressibleExt;
};

/**
 * One durable, replayable mesh-repair operation.
 *
 * The enabled passes run in a fixed order against a fresh copy of Source on
 * every recompute. This lets the GUI and automation expose one semantic
 * repair step instead of mutating the source or creating a chain of
 * implementation-only filters.
 */
class MeshExport Repair: public Mesh::FixDefects
{
    PROPERTY_HEADER_WITH_OVERRIDE(Mesh::Repair);

public:
    Repair();

    App::PropertyBool HarmonizeNormals;
    App::PropertyBool RemoveDuplicates;
    App::PropertyBool RemoveNonManifolds;
    App::PropertyBool RemoveNonManifoldPoints;
    App::PropertyBool FixIndices;
    App::PropertyBool FixDegenerations;
    App::PropertyBool FixSelfIntersections;
    App::PropertyBool RemoveFolds;
    App::PropertyIntegerConstraint FillHolesMaxEdges;
    App::PropertyBool Repeat;
    App::PropertyIntegerConstraint MaxIterations;

    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;

private:
    static const App::PropertyIntegerConstraint::Constraints nonNegativeInteger;
    static const App::PropertyIntegerConstraint::Constraints iterationRange;
};

/**
 * The HarmonizeNormals class harmonizes the normals of the faces.
 * @author Werner Mayer
 */
class MeshExport HarmonizeNormals: public Mesh::FixDefects
{
    PROPERTY_HEADER_WITH_OVERRIDE(Mesh::HarmonizeNormals);

public:
    /// Constructor
    HarmonizeNormals();

    /** @name methods override Feature */
    //@{
    /// recalculate the Feature
    App::DocumentObjectExecReturn* execute() override;
    //@}
};

/**
 * The FlipNormals class flips the normals of all faces.
 * @author Werner Mayer
 */
class MeshExport FlipNormals: public Mesh::FixDefects
{
    PROPERTY_HEADER_WITH_OVERRIDE(Mesh::FlipNormals);

public:
    /// Constructor
    FlipNormals();

    /** @name methods override Feature */
    //@{
    /// recalculate the Feature
    App::DocumentObjectExecReturn* execute() override;
    //@}
};

/**
 * The FixNonManifolds class tries to fix edges with more than two faces attached.
 * @author Werner Mayer
 */
class MeshExport FixNonManifolds: public Mesh::FixDefects
{
    PROPERTY_HEADER_WITH_OVERRIDE(Mesh::FixNonManifolds);

public:
    /// Constructor
    FixNonManifolds();
    App::PropertyBool RemoveNonManifoldPoints;

    /** @name methods override Feature */
    //@{
    /// recalculate the Feature
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    //@}
};

/**
 * The FixDuplicatedFaces class removes duplicated faces from the mesh.
 * @author Werner Mayer
 */
class MeshExport FixDuplicatedFaces: public Mesh::FixDefects
{
    PROPERTY_HEADER_WITH_OVERRIDE(Mesh::FixDuplicatedFaces);

public:
    /// Constructor
    FixDuplicatedFaces();

    /** @name methods override Feature */
    //@{
    /// recalculate the Feature
    App::DocumentObjectExecReturn* execute() override;
    //@}
};

/**
 * The FixDuplicatedPoints class removes duplicated points from the mesh.
 * @author Werner Mayer
 */
class MeshExport FixDuplicatedPoints: public Mesh::FixDefects
{
    PROPERTY_HEADER_WITH_OVERRIDE(Mesh::FixDuplicatedPoints);

public:
    /// Constructor
    FixDuplicatedPoints();

    /** @name methods override Feature */
    //@{
    /// recalculate the Feature
    App::DocumentObjectExecReturn* execute() override;
    //@}
};

/**
 * The FixDegenerations class removes degenerated faces from the mesh.
 * @author Werner Mayer
 */
class MeshExport FixDegenerations: public Mesh::FixDefects
{
    PROPERTY_HEADER_WITH_OVERRIDE(Mesh::FixDegenerations);

public:
    /// Constructor
    FixDegenerations();

    /** @name methods override Feature */
    //@{
    /// recalculate the Feature
    App::DocumentObjectExecReturn* execute() override;
    //@}
};

/**
 * The FixDeformations class tries to repair deformed faces by swapping edge operations.
 * @author Werner Mayer
 */
class MeshExport FixDeformations: public Mesh::FixDefects
{
    PROPERTY_HEADER_WITH_OVERRIDE(Mesh::FixDeformations);

public:
    /// Constructor
    FixDeformations();

    /** @name Properties */
    //@{
    App::PropertyFloat MaxAngle; /**< Maximum allowed angle */
    //@}
    /** @name methods override Feature */
    //@{
    /// recalculate the Feature
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    //@}
};

/**
 * The FixIndices class tries to validate invalid indices of the internal mesh data structure.
 * @author Werner Mayer
 */
class MeshExport FixIndices: public Mesh::FixDefects
{
    PROPERTY_HEADER_WITH_OVERRIDE(Mesh::FixIndices);

public:
    /// Constructor
    FixIndices();

    /** @name methods override Feature */
    //@{
    /// recalculate the Feature
    App::DocumentObjectExecReturn* execute() override;
    //@}
};

/**
 * The FillHoles class tries to fill up holes in the internal mesh data structure.
 * @author Werner Mayer
 */
class MeshExport FillHoles: public Mesh::FixDefects
{
    PROPERTY_HEADER_WITH_OVERRIDE(Mesh::FillHoles);

public:
    /// Constructor
    FillHoles();
    App::PropertyInteger FillupHolesOfLength;
    App::PropertyFloat MaxArea;
    App::PropertyEnumeration Method;

    /** @name methods override Feature */
    //@{
    /// recalculate the Feature
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    //@}

private:
    static const char* MethodEnums[];
};

/**
 * The RemoveComponents class removes topologic independent components up to a certain size from the
 * mesh data structure.
 * @author Werner Mayer
 */
class MeshExport RemoveComponents: public Mesh::FixDefects
{
    PROPERTY_HEADER_WITH_OVERRIDE(Mesh::RemoveComponents);

public:
    /// Constructor
    RemoveComponents();
    App::PropertyInteger RemoveCompOfSize;

    /** @name methods override Feature */
    //@{
    /// recalculate the Feature
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    //@}
};

}  // namespace Mesh
