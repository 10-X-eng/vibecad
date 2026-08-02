// SPDX-License-Identifier: LGPL-2.1-or-later
// SPDX-FileCopyrightText: 2026 Morten Vajhøj
// SPDX-FileNotice: Part of the FreeCAD project.

/******************************************************************************
 *                                                                            *
 *   FreeCAD is free software: you can redistribute it and/or modify          *
 *   it under the terms of the GNU Lesser General Public License as           *
 *   published by the Free Software Foundation, either version 2.1            *
 *   of the License, or (at your option) any later version.                   *
 *                                                                            *
 *   FreeCAD is distributed in the hope that it will be useful,               *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty              *
 *   of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.                  *
 *   See the GNU Lesser General Public License for more details.              *
 *                                                                            *
 *   You should have received a copy of the GNU Lesser General Public         *
 *   License along with FreeCAD. If not, see https://www.gnu.org/licenses     *
 *                                                                            *
 ******************************************************************************/

#pragma once

#include <QDialogButtonBox>
#include <QEvent>

#include <memory>
#include <cstdint>
#include <string>
#include <tuple>
#include <vector>

#include <Gui/Action.h>
#include <Gui/TaskView/TaskDialog.h>
#include <Gui/TaskView/TaskView.h>
#include <Gui/Selection/Selection.h>

#include <Base/Placement.h>

#include "Mod/Measure/App/MassPropertiesResult.h"

namespace MassPropertiesGui
{

class TaskMassPropertiesWidget;
class OwnedMassPropertiesTransaction;

class TaskMassProperties: public Gui::TaskView::TaskDialog, public Gui::SelectionObserver
{
public:
    TaskMassProperties();
    ~TaskMassProperties() override;

    void modifyStandardButtons(QDialogButtonBox* box) override;
    QDialogButtonBox::StandardButtons getStandardButtons() const override
    {
        return QDialogButtonBox::Apply | QDialogButtonBox::Abort | QDialogButtonBox::Reset;
    }

    void invoke();
    bool accept() override;
    bool reject() override;

    void onSelectionChanged(const Gui::SelectionChanges& msg) override;
    void update(const Gui::SelectionChanges& msg);
    void tryUpdate();

    void createDatum(const Base::Vector3d& position, const std::string& name, bool removeExisting = true);
    void createLCS(std::string name, bool removeExisting = true);

    void onCogDatumButtonPressed();
    void onCovDatumButtonPressed();
    void onLcsButtonPressed();
    void onCoordinateSystemChanged(MassPropertiesMode coordSystemMode);
    void onSelectCustomCoordinateSystem();
    void updateInertiaVisibility();

protected:
    bool eventFilter(QObject* watched, QEvent* event) override;

private:
    struct TrackedOccurrence
    {
        App::DocumentObject* root = nullptr;
        std::string subName;
    };

    App::Document* targetDocument() const;
    App::DocumentObject* currentDatumObject() const;
    void setCurrentDatumObject(App::DocumentObject* object);
    void clearCurrentDatumObject();
    App::DocumentObject* currentDatumOccurrenceRoot() const;
    void setCurrentDatumOccurrence(
        App::DocumentObject* root,
        const std::string& subName
    );
    void clearCurrentDatumOccurrence();
    App::DocumentObject* previewObject() const;
    void clearPreviewObjectIdentity();
    bool startPreviewTransaction();
    bool abortPreviewTransaction();
    bool finishDurableResult(
        std::unique_ptr<OwnedMassPropertiesTransaction> transaction
    );
    void escape();
    void removeTemporaryObjects();
    void clearUiFields();
    void saveResult();

    TaskMassPropertiesWidget* panel = nullptr;

    bool selectingCustomCoordSystem = false;
    bool isUpdating = false;
    int unitsSchemaIndex = -1;

    MassPropertiesData currentInfo;
    MassPropertiesMode currentMode {MassPropertiesMode::CenterOfGravity};
    std::string currentDatumDocumentName;
    std::string currentDatumDocumentUid;
    const App::Document* currentDatumDocumentAddress {nullptr};
    std::string currentDatumName;
    long currentDatumId {-1};
    std::string currentDatumOccurrenceRootName;
    long currentDatumOccurrenceRootId {-1};
    std::string currentDatumOccurrenceSubName;
    Base::Placement currentDatumPlacement;
    bool hasCurrentDatumPlacement = false;
    std::vector<std::tuple<std::string, std::string, std::string>>
        savedSelection;

    Gui::Action* deleteAction = nullptr;
    bool deleteActivated = false;

    std::vector<MassPropertiesInput> objectsToMeasure;
    std::vector<TrackedOccurrence> objectOccurrences;
    std::unique_ptr<OwnedMassPropertiesTransaction> previewTransaction;
    std::string targetDocumentName;
    std::string targetDocumentUid;
    const App::Document* targetDocumentAddress {nullptr};
    std::string previewObjectName;
    long previewObjectId {-1};
    std::uint64_t previewGeneration {0};
};

}  // namespace MassPropertiesGui
