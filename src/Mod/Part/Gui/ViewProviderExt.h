// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2011 Juergen Riegel <juergen.riegel@web.de>             *
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

#include "SoFCShapeObject.h"
#include "RenderMeshController.h"


#include <cstdint>
#include <map>
#include <memory>
#include <string>
#include <utility>

#include <App/PropertyUnits.h>
#include <Gui/ViewProviderGeometryObject.h>
#include <Gui/ViewProviderTextureExtension.h>

#include <Mod/Part/App/PartFeature.h>
#include <Mod/Part/PartGlobal.h>


class TopoDS_Shape;
class TopoDS_Edge;
class TopoDS_Wire;
class TopoDS_Face;
class SoSeparator;
class SoGroup;
class SoSwitch;
class SoVertexShape;
class SoPickedPoint;
class SoShapeHints;
class SoEventCallback;
class SbVec3f;
class SoSphere;
class SoScale;
class SoCoordinate3;
class SoIndexedFaceSet;
class SoNormal;
class SoNormalBinding;
class SoMaterialBinding;
class SoIndexedLineSet;

namespace Part
{
struct RenderMesh;
}

namespace PartGui
{

class SoBrepFaceSet;
class SoBrepEdgeSet;
class SoBrepPointSet;

class PartGuiExport ViewProviderPartExt: public Gui::ViewProviderGeometryObject
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartGui::ViewProviderPartExt);

public:
    /// constructor
    ViewProviderPartExt();
    /// destructor
    ~ViewProviderPartExt() override;

    // Display properties
    App::PropertyFloatConstraint Deviation;
    App::PropertyBool ControlPoints;
    App::PropertyAngle AngularDeflection;
    App::PropertyEnumeration Lighting;
    App::PropertyEnumeration DrawStyle;
    // Points
    App::PropertyFloatConstraint PointSize;
    App::PropertyColor PointColor;
    App::PropertyMaterial PointMaterial;
    App::PropertyColorList PointColorArray;
    // Lines
    App::PropertyFloatConstraint LineWidth;
    App::PropertyColor LineColor;
    App::PropertyMaterial LineMaterial;
    App::PropertyColorList LineColorArray;

    void attach(App::DocumentObject*) override;
    void setDisplayMode(const char* ModeName) override;
    /// returns a list of all possible modes
    std::vector<std::string> getDisplayModes() const override;
    /// Update the view representation
    void reload();
    /// If no other task is pending it opens a dialog to allow one to change face colors
    bool changeFaceAppearances();

    void updateData(const App::Property*) override;

    /** @name Restoring view provider from document load */
    //@{
    void startRestoring() override;
    void finishRestoring() override;
    /// Build a deferred visible shape after document restoration has completed.
    void finishDeferredVisualRestore();
    //@}

    /** @name Selection handling
     * This group of methods do the selection handling.
     * Here you can define how the selection for your ViewProfider
     * works.
     */
    //@{
    /// indicates if the ViewProvider use the new Selection model
    bool useNewSelectionModel() const override
    {
        return true;
    }
    /// return a hit element to the selection path or 0
    std::string getElement(const SoDetail*) const override;
    SoDetail* getDetail(const char*) const override;
    std::vector<Base::Vector3d> getModelPoints(const SoPickedPoint*) const override;
    /// return the highlight lines for a given element or the whole shape
    std::vector<Base::Vector3d> getSelectionShape(const char* Element) const override;
    //@}

    virtual Part::TopoShape getRenderedShape() const
    {
        return Part::Feature::getTopoShape(
            getObject(),
            Part::ShapeOption::ResolveLink | Part::ShapeOption::Transform
        );
    }

    /** Capture the installed native geometry on the GUI owner, without reading
     * document properties. The reference survives view removal. Treat shared
     * geometry as read-only and make a private copy before kernel mutation.
     * Returns a null shape until a rendered generation is available.
     */
    std::shared_ptr<const Part::RenderMesh> getRenderedMeshSnapshot() const
    {
        return installedRenderMesh;
    }

    TopoDS_Shape getRenderedShapeSnapshot() const
    {
        return lastRenderedShape;
    }

    /** @name Highlight handling
     * This group of methods do the highlighting of elements.
     */
    //@{
    void setHighlightedFaces(const std::vector<App::Material>& materials);
    void setHighlightedFaces(const App::PropertyMaterialList& appearance);
    void unsetHighlightedFaces();
    void setHighlightedEdges(const std::vector<Base::Color>& colors);
    void unsetHighlightedEdges();
    void setHighlightedPoints(const std::vector<Base::Color>& colors);
    void unsetHighlightedPoints();
    //@}

    /** @name Color management methods
     */
    //@{
    std::map<std::string, Base::Color> getElementColors(const char* element = nullptr) const override;
    //@}

    bool isUpdateForced() const override
    {
        return forceUpdateCount > 0;
    }
    void forceUpdate(bool enable = true) override;

    bool allowOverride(const App::DocumentObject&) const override;

    void setFaceHighlightActive(bool active)
    {
        faceHighlightActive = active;
    }
    bool isFaceHighlightActive() const
    {
        return faceHighlightActive;
    }

    /** @name Edit methods */
    //@{
    void setupContextMenu(QMenu*, QObject*, const char*) override;

    /// Get the python wrapper for that ViewProvider
    PyObject* getPyObject() override;

    /// configures Coin nodes so they render given toposhape
    static void setupCoinGeometry(
        TopoDS_Shape shape,
        SoCoordinate3* coords,
        SoBrepFaceSet* faceset,
        SoNormal* norm,
        SoBrepEdgeSet* lineset,
        SoBrepPointSet* nodeset,
        double deviation,
        double angularDeflection,
        bool normalsFromUV = false
    );

    static void setupCoinGeometry(
        TopoDS_Shape shape,
        SoFCShape* node,
        double deviation,
        double angularDeflection,
        bool normalsFromUV = false
    );

protected:
    bool setEdit(int ModNum) override;
    void unsetEdit(int ModNum) override;
    //@}

protected:
    /// get called by the container whenever a property has been changed
    void onChanged(const App::Property* prop) override;
    bool loadParameter();
    void updateVisual();
    void handleChangedPropertyName(
        Base::XMLReader& reader,
        const char* TypeName,
        const char* PropName
    ) override;

    // nodes for the data representation
    SoMaterialBinding* pcFaceBind;
    SoMaterialBinding* pcLineBind;
    SoMaterialBinding* pcPointBind;
    SoMaterial* pcLineMaterial;
    SoMaterial* pcPointMaterial;
    SoDrawStyle* pcLineStyle;
    SoDrawStyle* pcPointStyle;
    SoShapeHints* pShapeHints;

    SoCoordinate3* coords;
    SoBrepFaceSet* faceset;
    SoNormal* norm;
    SoNormalBinding* normb;
    SoBrepEdgeSet* lineset;
    SoBrepPointSet* nodeset;

    bool VisualTouched;
    bool NormalsFromUV;
    bool faceHighlightActive = false;

private:
    friend struct DeferredVisual;
    void applyShapeAppearance();
    void applyEdgeAppearance();
    void applyPointAppearance();
    using AppearanceStamp = std::pair<std::uint64_t, std::uint64_t>;
    AppearanceStamp shapeAppearanceStamp {};
    AppearanceStamp edgeAppearanceStamp {};
    AppearanceStamp pointAppearanceStamp {};
    int appearanceFaceCount {-1};
    void startVisualBuild(TopoDS_Shape shape, std::uint64_t generation);
    void finishVisualBuild(
        std::uint64_t instanceId,
        std::uint64_t generation,
        TopoDS_Shape shape,
        std::shared_ptr<const Part::RenderMesh> mesh,
        std::string error
    );
    void bindRenderMesh(std::shared_ptr<const Part::RenderMesh> mesh);
    Gui::ViewProviderFaceTexture texture;
    // settings stuff
    int forceUpdateCount;
    static App::PropertyFloatConstraint::Constraints sizeRange;
    static App::PropertyFloatConstraint::Constraints tessRange;
    static App::PropertyQuantityConstraint::Constraints angDeflectionRange;
    static const char* LightingEnums[];
    static const char* DrawStyleEnums[];

    // This is needed to restore old DiffuseColor values since the restore
    // function is asynchronous
    App::PropertyColorList _diffuseColor;

    // shape that was last rendered so if it does not change we don't re-render it without need
    TopoDS_Shape lastRenderedShape;
    std::uint64_t visualInstanceId {0};
    std::uint64_t visualRequestGeneration {0};
    bool visualBuildInFlight {false};
    RenderMeshController visualMeshController;
    bool deferredVisualRestorePending {false};
    // Coin borrows the prepared arrays through setValuesPointer(). Keep the
    // exact immutable artifact alive until every field is rebound or this
    // provider is destroyed.
    std::shared_ptr<const Part::RenderMesh> installedRenderMesh;
};

}  // namespace PartGui
