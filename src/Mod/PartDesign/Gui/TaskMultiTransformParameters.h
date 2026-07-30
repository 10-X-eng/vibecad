// SPDX-License-Identifier: LGPL-2.1-or-later

/******************************************************************************
 *   Copyright (c) 2012 Jan Rheinländer <jrheinlaender@users.sourceforge.net> *
 *                                                                            *
 *   This file is part of the FreeCAD CAx development system.                 *
 *                                                                            *
 *   This library is free software; you can redistribute it and/or            *
 *   modify it under the terms of the GNU Library General Public              *
 *   License as published by the Free Software Foundation; either             *
 *   version 2 of the License, or (at your option) any later version.         *
 *                                                                            *
 *   This library  is distributed in the hope that it will be useful,         *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of           *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the            *
 *   GNU Library General Public License for more details.                     *
 *                                                                            *
 *   You should have received a copy of the GNU Library General Public        *
 *   License along with this library; see the file COPYING.LIB. If not,       *
 *   write to the Free Software Foundation, Inc., 59 Temple Place,            *
 *   Suite 330, Boston, MA  02111-1307, USA                                   *
 *                                                                            *
 ******************************************************************************/

#pragma once

#include <cstddef>
#include <string>
#include <vector>

#include "TaskTransformedParameters.h"
#include "ViewProviderMultiTransform.h"


class QAction;
class Ui_TaskMultiTransformParameters;
class QModelIndex;

namespace PartDesign
{
class MultiTransform;
class Transformed;
}

namespace App
{
class DocumentObject;
class Property;
}

namespace Gui
{
class ViewProvider;
}

namespace PartDesignGui
{


class TaskMultiTransformParameters: public TaskTransformedParameters
{
    Q_OBJECT

public:
    explicit TaskMultiTransformParameters(
        ViewProviderTransformed* TransformedView,
        QWidget* parent = nullptr
    );
    ~TaskMultiTransformParameters() override;

    void apply() override;

    /// Return the currently active subFeature
    PartDesign::Transformed* getSubFeature()
    {
        subFeature = resolveSubFeature();
        return subFeature;
    }

private Q_SLOTS:
    /// User finished editing a subFeature
    void onSubTaskButtonOK();
    void onTransformDelete();
    void onTransformEdit();
    void onTransformActivated(const QModelIndex& index);
    void onTransformAddMirrored();
    void onTransformAddLinearPattern();
    void onTransformAddPolarPattern();
    void onTransformAddScaled();
    void onMoveUp();
    void onMoveDown();
    // Note: There is no Cancel button because I couldn't work out how to save the state of
    // a subFeature so as to revert the changes of an edit operation
    void onUpdateView(bool /*unused*/) override;

private:
    void setupParameterUI(QWidget* widget) override;
    void retranslateParameterUI(QWidget* widget) override;

    /** Notifies when the object is about to be removed. */
    void slotDeletedObject(const Gui::ViewProviderDocumentObject& Obj) override;
    /** Keeps the row identities synchronized with the live Transformations property. */
    void slotChangedObject(
        const Gui::ViewProviderDocumentObject& Obj,
        const App::Property& Prop
    ) override;
    void slotRelabelObject(const Gui::ViewProviderDocumentObject& Obj) override;

    void updateUI();
    void closeSubTask();
    void moveTransformFeature(int increment);
    void finishAdd(PartDesign::Transformed* newFeature);
    void scheduleTransformListRefresh();
    void rebuildTransformList(long preferredObjectId = -1);
    void updateOperationState();
    bool ensureTransformListSynchronized();
    bool transformListMatches(const PartDesign::MultiTransform* multiTransform) const;
    bool isLiveTransformation(
        const PartDesign::MultiTransform* multiTransform,
        const App::DocumentObject* object
    ) const;
    bool isOwnedTransformation(
        const PartDesign::MultiTransform* multiTransform,
        const PartDesign::Transformed* transformation
    ) const;
    PartDesign::Transformed* transformationForRow(
        PartDesign::MultiTransform* multiTransform,
        int row,
        const std::vector<App::DocumentObject*>& transformations,
        std::size_t& propertyIndex
    ) const;
    PartDesign::MultiTransform* resolveMultiTransform();
    PartDesign::Transformed* resolveSubFeature() const;
    void rememberSubFeature(PartDesign::Transformed* transformation);
    void recomputeMultiTransform(PartDesign::MultiTransform* multiTransform);

private:
    std::unique_ptr<Ui_TaskMultiTransformParameters> ui;
    QAction* editAction = nullptr;
    QAction* deleteAction = nullptr;
    QAction* addMirroredAction = nullptr;
    QAction* addLinearAction = nullptr;
    QAction* addPolarAction = nullptr;
    QAction* addScaledAction = nullptr;
    QAction* moveUpAction = nullptr;
    QAction* moveDownAction = nullptr;
    /// The subTask and subFeature currently active in the UI
    TaskTransformedParameters* subTask = nullptr;
    PartDesign::Transformed* subFeature = nullptr;
    std::string multiTransformDocumentName;
    std::string multiTransformObjectName;
    long multiTransformObjectId = -1;
    std::string subFeatureDocumentName;
    std::string subFeatureObjectName;
    long subFeatureObjectId = -1;
    bool refreshScheduled = false;
    bool editHint = false;
};


/// simulation dialog for the TaskView
class TaskDlgMultiTransformParameters: public TaskDlgTransformedParameters
{
    Q_OBJECT

public:
    explicit TaskDlgMultiTransformParameters(ViewProviderMultiTransform* MultiTransformView);

protected:
    void finalizeAcceptedFeature(App::DocumentObject* feature) override;
};

}  // namespace PartDesignGui
