// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2010 Juergen Riegel <FreeCAD@juergen-riegel.net>        *
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

#include <App/PropertyStandard.h>
#include <Mod/Part/App/BodyBase.h>
#include <Mod/PartDesign/PartDesignGlobal.h>

namespace App
{
class Origin;
}

namespace PartDesign
{

class Feature;

class PartDesignExport Body: public Part::BodyBase
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::Body);

public:
    App::PropertyBool AllowCompound;
    App::PropertyUUID VibeCADBodyId;
    App::PropertyUUID DesignId;
    App::PropertyString ComponentId;

    /// True if this body feature is active or was active when the document was last closed
    // App::PropertyBool IsActive;

    Body();

    /** @name methods override feature */
    //@{
    /// recalculate the feature
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;

    /// returns the type name of the view provider
    const char* getViewProviderName() const override
    {
        return "PartDesignGui::ViewProviderBody";
    }

    App::DocumentObject* getModelingState() override;
    const App::DocumentObject* getModelingState() const override;
    const App::DocumentObject* getModelingPresentation() const override;
    bool containsModelingState(const App::DocumentObject* object) const override;
    //@}

    /**
     * Add the feature into the body at the current insert point.
     * The insertion point is the before next solid after the Tip feature
     */
    std::vector<App::DocumentObject*> addObject(App::DocumentObject*) override;
    std::vector<DocumentObject*> addObjects(std::vector<DocumentObject*> obj) override;

    /**
     * Insert the feature into the body after the given feature.
     *
     * @param feature  The feature to insert into the body
     * @param target   The feature relative which one should be inserted the given.
     *                 If target is NULL than insert into the end if where is InsertBefore
     *                 and into the begin if where is InsertAfter.
     * @param after    if true insert the feature after the target. Default is false.
     *
     * @note the method doesn't modify the Tip unlike addObject()
     */
    void insertObject(App::DocumentObject* feature, App::DocumentObject* target, bool after = false);

    void setBaseProperty(App::DocumentObject* feature);

    /// Remove the feature from the body
    std::vector<DocumentObject*> removeObject(DocumentObject* obj) override;

    /**
     * Checks if the given document object lays after the current insert point
     * (place before next solid after the Tip)
     */
    bool isAfterInsertPoint(App::DocumentObject* feature);

    /**
     * Return true if the given feature is a legacy Part Design solid-sequence feature.
     *
     * This preserves the historical public contract. New code that needs to identify nodes in the
     * consolidated Body result sequence must use isResultFeature().
     */
    static bool isSolidFeature(const App::DocumentObject* obj);

    /**
     * Return true if the object participates in the Body's ordered result sequence.
     *
     * This includes Part Design material features and ordinary Part shape results. Sketches,
     * datums, Body containers, and other support objects are not result features.
     */
    static bool isResultFeature(const App::DocumentObject* obj);

    /**
     * Return true if the given feature is allowed in a Body. Currently allowed are
     * all features derived from PartDesign::Feature and Part::Datum and sketches
     */
    static bool isAllowed(const App::DocumentObject* obj);
    bool allowObject(DocumentObject* obj) override
    {
        return isAllowed(obj);
    }

    /**
     * Return the body which this feature belongs too, or NULL
     * The only difference to BodyBase::findBodyOf() is that this one casts value to Body*
     */
    static Body* findBodyOf(const App::DocumentObject* feature);

    PyObject* getPyObject() override;

    std::vector<std::string> getSubObjects(int reason = 0) const override;
    App::DocumentObject* getSubObject(
        const char* subname,
        PyObject** pyObj,
        Base::Matrix4D* pmat,
        bool transform,
        int depth
    ) const override;

    void setShowTip(bool enable)
    {
        showTip = enable;
    }

    /** Return the result feature before the given feature, or before the Tip feature. */
    App::DocumentObject* getPrevResultFeature(App::DocumentObject* start = nullptr);

    /** Compatibility name for getPrevResultFeature(). */
    App::DocumentObject* getPrevSolidFeature(App::DocumentObject* start = nullptr);

    /** Return the next result feature after the given feature, or after the Tip feature. */
    App::DocumentObject* getNextResultFeature(App::DocumentObject* start = nullptr);

    /** Compatibility name for getNextResultFeature(). */
    App::DocumentObject* getNextSolidFeature(App::DocumentObject* start = nullptr);

    /// Return true when any Body result contains solid topology.
    bool isSolid();

protected:
    void onSettingDocument() override;

    /// Adjusts the first solid's feature's base on BaseFeature getting set
    void onChanged(const App::Property* prop) override;

    /// Creates the corresponding Origin object
    void setupObject() override;
    /// Removes all planes and axis if they are still linked to the document
    void unsetupObject() override;

    void onDocumentRestored() override;

private:
    fastsignals::scoped_connection connection;
    bool showTip = false;
};

}  // namespace PartDesign
