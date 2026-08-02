// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <string>
#include <vector>

#include <App/PropertyLinks.h>
#include <Base/Vector3D.h>
#include <Gui/TaskView/TaskView.h>
#include <Mod/PartDesign/App/DesignModel.h>

class QComboBox;
class QCheckBox;
class QDoubleSpinBox;
class QLabel;
class QListWidget;
class QPushButton;
class QSpinBox;

namespace App
{
class DocumentObject;
class Part;
}  // namespace App

namespace Part
{
class Part2DObject;
}

namespace PartDesign
{
class Body;
class DesignBodyState;
class DesignOperationProperties;
class ProfileBased;
}  // namespace PartDesign

namespace PartDesignGui
{

/** Select exact closed areas from one reusable sketch. */
class TaskDesignProfileRegions: public Gui::TaskView::TaskBox
{
    Q_OBJECT

public:
    explicit TaskDesignProfileRegions(App::DocumentObject* operation, QWidget* parent = nullptr);
    ~TaskDesignProfileRegions() override;

    /** Apply any in-progress viewport selection before task acceptance. */
    void finalize();

private Q_SLOTS:
    void toggleRegionSelection(bool selecting);
    void useEntireSketch();

private:
    PartDesign::ProfileBased* profileOperation() const;
    bool applyViewportSelection();
    bool setProfile(Part::Part2DObject& sketch, const std::vector<std::string>& regions);
    void populate();
    void restoreSelectionSketchVisibility();
    void setError(const QString& message);

    App::DocumentObject* operation {};
    QLabel* sketchName {};
    QLabel* regionSummary {};
    QLabel* instruction {};
    QPushButton* selectRegions {};
    QPushButton* entireSketch {};
    std::string selectionSketchName;
    bool selectionSketchWasVisible {false};
};

/**
 * Explicit result and Body-target selection shared by every Design-global
 * profile operation.
 *
 * This is not an active-Body adapter. It writes the operation's complete
 * Design contract directly: result mode, exact Body identities, exact prior
 * Body states, and target coordinate frames.
 */
class TaskDesignOperationTargets: public Gui::TaskView::TaskBox
{
    Q_OBJECT

public:
    explicit TaskDesignOperationTargets(App::DocumentObject* operation, QWidget* parent = nullptr);
    ~TaskDesignOperationTargets() override;

    /** Validate and atomically publish the operation's Body-state graph. */
    void finalize();

private Q_SLOTS:
    void applySelection();
    void addSelectedSplitDefinitions();
    void removeSelectedSplitDefinitions();
    void useSelectedPatternReference();
    void clearPatternReference();

private:
    void populate();
    void populatePatternSources();
    void populatePatternParameters();
    void populateScaleParameters();
    void configureOperation();
    void configurePattern();
    void configureScale();
    void updatePatternReferenceLabel();
    void updateCombineBodyRows();
    void populateSplitRows(const std::vector<App::PropertyLinkSubList::SubSet>& references);
    void populateSeparateRows();

    PartDesign::DesignOperationProperties* operationProperties() const;
    std::vector<PartDesign::Body*> selectedBodies() const;
    std::vector<PartDesign::Body*> selectedToolBodies() const;
    PartDesign::Body* selectedResultBody() const;
    PartDesign::Body* selectedPatternSourceBody() const;
    App::DocumentObject* selectedPatternSourceOperation() const;
    App::Part* selectedDestinationComponent() const;
    std::size_t generatedPatternCopyCount() const;

    App::DocumentObject* operation {};
    QComboBox* resultMode {};
    QComboBox* destinationComponent {};
    QComboBox* resultBody {};
    QComboBox* retainedRegion {};
    QCheckBox* keepTools {};
    QListWidget* targetBodies {};
    QPushButton* addSplitDefinitions {};
    QPushButton* removeSplitDefinitions {};
    QComboBox* patternSourceMode {};
    QComboBox* patternSourceObject {};
    QLabel* patternReference {};
    QPushButton* usePatternReference {};
    QPushButton* clearPatternReferenceButton {};
    QSpinBox* patternOccurrences {};
    QLabel* patternOccurrencesLabel {};
    QDoubleSpinBox* patternPrimaryValue {};
    QCheckBox* patternOption {};
    QLabel* patternPrimaryLabel {};
    QLabel* patternOriginLabel {};
    QLabel* patternDirectionLabel {};
    QCheckBox* scaleUniform {};
    QDoubleSpinBox* scaleUniformFactor {};
    QDoubleSpinBox* scaleXFactor {};
    QDoubleSpinBox* scaleYFactor {};
    QDoubleSpinBox* scaleZFactor {};
    QLabel* separateSummary {};
    QWidget* patternOriginEditor {};
    QWidget* patternDirectionEditor {};
    QWidget* scaleCenterEditor {};
    std::vector<QDoubleSpinBox*> patternOriginValues;
    std::vector<QDoubleSpinBox*> patternDirectionValues;
    std::vector<QDoubleSpinBox*> scaleCenterValues;
    std::vector<App::Part*> components;
    std::vector<PartDesign::Body*> bodies;
    PartDesign::DesignOperationEdit edit;
    bool populating {false};
    bool fixedModifyMode {false};
    bool fixedResultMode {false};
    bool combineMode {false};
    bool splitMode {false};
    bool separateMode {false};
    bool patternMode {false};
    bool scaleMode {false};
    std::vector<Base::Vector3d> splitWitnesses;
};

}  // namespace PartDesignGui
