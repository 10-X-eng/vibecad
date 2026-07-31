// SPDX-License-Identifier: LGPL-2.1-or-later

#include "TaskDesignOperation.h"

#include <QCheckBox>
#include <QComboBox>
#include <QDoubleSpinBox>
#include <QFormLayout>
#include <QGridLayout>
#include <QHBoxLayout>
#include <QLabel>
#include <QListWidget>
#include <QMessageBox>
#include <QPushButton>
#include <QSignalBlocker>
#include <QSpinBox>
#include <QVBoxLayout>

#include <Standard_Failure.hxx>

#include <algorithm>
#include <ranges>
#include <sstream>
#include <string_view>
#include <unordered_map>
#include <unordered_set>

#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <App/GeoFeature.h>
#include <App/Part.h>
#include <App/PropertyLinks.h>
#include <Base/Exception.h>
#include <Base/Uuid.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/MainWindow.h>
#include <Gui/Selection/Selection.h>
#include <Mod/Part/App/PartFeature.h>
#include <Mod/Part/Gui/ModelingSelection.h>
#include <Mod/PartDesign/App/Body.h>
#include <Mod/PartDesign/App/DesignFeature.h>
#include <Mod/PartDesign/App/DesignModel.h>

#include "ReferenceSelection.h"
#include "Utils.h"

using namespace PartDesignGui;

namespace
{

QString bodyDisplayName(const PartDesign::Body& body)
{
    QString result = QString::fromUtf8(body.Label.getValue());
    if (const auto* component = App::Part::getPartOfObject(&body);
        component && component->Type.getStrValue() == "Component") {
        result = QStringLiteral("%1 / %2").arg(QString::fromUtf8(component->Label.getValue()), result);
    }
    return result;
}

}  // namespace

TaskDesignOperationTargets::TaskDesignOperationTargets(App::DocumentObject* operation, QWidget* parent)
    : TaskBox(Gui::BitmapFactory().pixmap("PartDesign_Body"), tr("Result"), true, parent)
    , operation(operation)
    , resultMode(new QComboBox(this))
    , destinationComponent(new QComboBox(this))
    , resultBody(new QComboBox(this))
    , retainedRegion(new QComboBox(this))
    , keepTools(new QCheckBox(tr("Keep tool Bodies"), this))
    , targetBodies(new QListWidget(this))
    , addSplitDefinitions(new QPushButton(tr("Add Selected"), this))
    , removeSplitDefinitions(new QPushButton(tr("Remove"), this))
    , patternSourceMode(new QComboBox(this))
    , patternSourceObject(new QComboBox(this))
    , patternReference(new QLabel(this))
    , usePatternReference(new QPushButton(tr("Use Selection"), this))
    , clearPatternReferenceButton(new QPushButton(tr("Clear"), this))
    , patternOccurrences(new QSpinBox(this))
    , patternOccurrencesLabel(new QLabel(tr("Occurrences"), this))
    , patternPrimaryValue(new QDoubleSpinBox(this))
    , patternOption(new QCheckBox(this))
    , patternPrimaryLabel(new QLabel(this))
    , patternOriginLabel(new QLabel(this))
    , patternDirectionLabel(new QLabel(this))
    , scaleUniform(new QCheckBox(tr("Uniform"), this))
    , scaleUniformFactor(new QDoubleSpinBox(this))
    , scaleXFactor(new QDoubleSpinBox(this))
    , scaleYFactor(new QDoubleSpinBox(this))
    , scaleZFactor(new QDoubleSpinBox(this))
    , separateSummary(new QLabel(this))
    , patternOriginEditor(new QWidget(this))
    , patternDirectionEditor(new QWidget(this))
    , scaleCenterEditor(new QWidget(this))
{
    if (!operation || !operation->getDocument() || !operationProperties()) {
        throw Base::TypeError("The result selector requires one live Design operation");
    }
    fixedModifyMode = dynamic_cast<PartDesign::DesignSubelementOperationProperties*>(operation);
    fixedResultMode = operationProperties()->ResultOperation.isReadOnly();
    combineMode = freecad_cast<PartDesign::DesignCombine*>(operation) != nullptr;
    splitMode = freecad_cast<PartDesign::DesignSplit*>(operation) != nullptr;
    separateMode = freecad_cast<PartDesign::DesignSeparate*>(operation) != nullptr;
    patternMode = dynamic_cast<PartDesign::DesignPatternProperties*>(operation) != nullptr;
    scaleMode = freecad_cast<PartDesign::DesignScale*>(operation) != nullptr;

    resultBody->setObjectName(QStringLiteral("DesignResultBody"));
    retainedRegion->setObjectName(QStringLiteral("DesignSplitRetainedRegion"));
    targetBodies->setObjectName(QStringLiteral("DesignBodyList"));
    addSplitDefinitions->setObjectName(QStringLiteral("DesignSplitAddDefinitions"));
    removeSplitDefinitions->setObjectName(QStringLiteral("DesignSplitRemoveDefinitions"));
    patternSourceMode->setObjectName(QStringLiteral("DesignPatternSourceMode"));
    patternSourceObject->setObjectName(QStringLiteral("DesignPatternSourceObject"));
    patternOccurrences->setObjectName(QStringLiteral("DesignPatternOccurrences"));
    patternPrimaryValue->setObjectName(QStringLiteral("DesignPatternPrimaryValue"));
    usePatternReference->setObjectName(QStringLiteral("DesignPatternUseReference"));
    clearPatternReferenceButton->setObjectName(QStringLiteral("DesignPatternClearReference"));
    scaleUniform->setObjectName(QStringLiteral("DesignScaleUniform"));
    scaleUniformFactor->setObjectName(QStringLiteral("DesignScaleUniformFactor"));
    scaleXFactor->setObjectName(QStringLiteral("DesignScaleXFactor"));
    scaleYFactor->setObjectName(QStringLiteral("DesignScaleYFactor"));
    scaleZFactor->setObjectName(QStringLiteral("DesignScaleZFactor"));
    removeSplitDefinitions->setEnabled(false);
    patternOccurrences->setRange(2, 10000);
    patternPrimaryValue->setDecimals(6);
    patternPrimaryValue->setRange(0.000001, 1.0e9);
    patternPrimaryValue->setSingleStep(1.0);

    const auto setupVectorEditor =
        [this](QWidget* editor, const char* role, std::vector<QDoubleSpinBox*>& values) {
            auto* vectorLayout = new QHBoxLayout(editor);
            vectorLayout->setContentsMargins(0, 0, 0, 0);
            static constexpr const char* names[] = {"X", "Y", "Z"};
            for (const char* name : names) {
                auto* value = new QDoubleSpinBox(editor);
                value->setObjectName(QStringLiteral("DesignPattern%1%2")
                                         .arg(QString::fromLatin1(role), QString::fromLatin1(name)));
                value->setDecimals(6);
                value->setRange(-1.0e9, 1.0e9);
                value->setSingleStep(1.0);
                value->setPrefix(QString::fromLatin1(name) + QStringLiteral(": "));
                vectorLayout->addWidget(value);
                values.push_back(value);
            }
        };
    setupVectorEditor(patternOriginEditor, "Origin", patternOriginValues);
    setupVectorEditor(patternDirectionEditor, "Direction", patternDirectionValues);

    auto* scaleCenterLayout = new QHBoxLayout(scaleCenterEditor);
    scaleCenterLayout->setContentsMargins(0, 0, 0, 0);
    static constexpr const char* scaleAxes[] = {"X", "Y", "Z"};
    for (const char* axis : scaleAxes) {
        auto* value = new QDoubleSpinBox(scaleCenterEditor);
        value->setObjectName(
            QStringLiteral("DesignScaleCenter%1").arg(QString::fromLatin1(axis))
        );
        value->setDecimals(6);
        value->setRange(-1.0e9, 1.0e9);
        value->setSingleStep(1.0);
        value->setPrefix(QString::fromLatin1(axis) + QStringLiteral(": "));
        scaleCenterLayout->addWidget(value);
        scaleCenterValues.push_back(value);
    }
    for (auto* factor : {scaleUniformFactor, scaleXFactor, scaleYFactor, scaleZFactor}) {
        factor->setDecimals(6);
        factor->setRange(0.000001, 1.0e6);
        factor->setSingleStep(0.1);
    }

    auto* proxy = new QWidget(this);
    auto* layout = new QVBoxLayout(proxy);
    if (scaleMode) {
        auto* form = new QFormLayout();
        form->addRow(QString(), scaleUniform);
        form->addRow(tr("Factor"), scaleUniformFactor);
        form->addRow(tr("X factor"), scaleXFactor);
        form->addRow(tr("Y factor"), scaleYFactor);
        form->addRow(tr("Z factor"), scaleZFactor);
        form->addRow(tr("Center"), scaleCenterEditor);
        layout->addLayout(form);
    }
    else if (patternMode) {
        auto* form = new QFormLayout();
        form->addRow(tr("Pattern"), patternSourceMode);
        form->addRow(tr("Source"), patternSourceObject);
        form->addRow(patternPrimaryLabel, patternPrimaryValue);
        form->addRow(patternOccurrencesLabel, patternOccurrences);
        form->addRow(patternOriginLabel, patternOriginEditor);
        form->addRow(patternDirectionLabel, patternDirectionEditor);
        form->addRow(QString(), patternOption);
        layout->addLayout(form);

        auto* referenceRow = new QHBoxLayout();
        referenceRow->addWidget(patternReference, 1);
        referenceRow->addWidget(usePatternReference);
        referenceRow->addWidget(clearPatternReferenceButton);
        layout->addLayout(referenceRow);
    }
    else if (combineMode) {
        auto* form = new QFormLayout();
        form->addRow(tr("Operation"), resultMode);
        form->addRow(tr("Result Body"), resultBody);
        layout->addLayout(form);
        layout->addWidget(keepTools);
    }
    else if (splitMode) {
        auto* form = new QFormLayout();
        form->addRow(tr("Source Body"), resultBody);
        form->addRow(tr("Keep identity on"), retainedRegion);
        layout->addLayout(form);
        resultBody->setEnabled(false);
    }
    else if (separateMode) {
        auto* separate = freecad_cast<PartDesign::DesignSeparate*>(operation);
        auto* source = separate ? separate->Source.getValue() : nullptr;
        auto* form = new QFormLayout();
        form->addRow(
            tr("Source"),
            new QLabel(
                source ? QString::fromUtf8(source->Label.getValue()) : tr("Missing definition"),
                proxy
            )
        );
        form->addRow(tr("New Bodies in"), destinationComponent);
        layout->addLayout(form);
        separateSummary->setWordWrap(true);
        separateSummary->setObjectName(QStringLiteral("DesignSeparateSummary"));
        layout->addWidget(separateSummary);
    }
    else if (!fixedModifyMode) {
        auto* form = new QFormLayout();
        form->addRow(tr("Operation"), resultMode);
        form->addRow(tr("New body in"), destinationComponent);
        layout->addLayout(form);
    }
    layout->addWidget(new QLabel(
        scaleMode             ? tr("Affected Bodies")
            : patternMode     ? tr("Target Bodies for a Feature Pattern")
            : fixedModifyMode ? tr("Affected Bodies")
            : combineMode     ? tr("Tool Bodies")
            : splitMode       ? tr("Splitting definitions")
            : separateMode    ? tr("Output Bodies")
                              : tr("Bodies"),
        proxy
    ));
    layout->addWidget(targetBodies);
    if (splitMode) {
        auto* buttons = new QHBoxLayout();
        buttons->addWidget(addSplitDefinitions);
        buttons->addWidget(removeSplitDefinitions);
        layout->addLayout(buttons);
    }
    groupLayout()->addWidget(proxy);

    edit = PartDesign::DesignModel::beginOperationEdit(*operation);
    populate();
    resultMode->setEnabled(!fixedResultMode);

    connect(resultMode, &QComboBox::currentIndexChanged, this, &TaskDesignOperationTargets::applySelection);
    connect(
        retainedRegion,
        &QComboBox::currentIndexChanged,
        this,
        &TaskDesignOperationTargets::applySelection
    );
    connect(resultBody, &QComboBox::currentIndexChanged, this, &TaskDesignOperationTargets::applySelection);
    connect(keepTools, &QCheckBox::toggled, this, &TaskDesignOperationTargets::applySelection);
    connect(
        destinationComponent,
        &QComboBox::currentIndexChanged,
        this,
        &TaskDesignOperationTargets::applySelection
    );
    connect(targetBodies, &QListWidget::itemChanged, this, &TaskDesignOperationTargets::applySelection);
    if (scaleMode) {
        connect(scaleUniform, &QCheckBox::toggled, this, [this](bool uniform) {
            scaleUniformFactor->setEnabled(uniform);
            scaleXFactor->setEnabled(!uniform);
            scaleYFactor->setEnabled(!uniform);
            scaleZFactor->setEnabled(!uniform);
            applySelection();
        });
        for (auto* factor : {scaleUniformFactor, scaleXFactor, scaleYFactor, scaleZFactor}) {
            connect(
                factor,
                &QDoubleSpinBox::valueChanged,
                this,
                &TaskDesignOperationTargets::applySelection
            );
        }
        for (auto* value : scaleCenterValues) {
            connect(
                value,
                &QDoubleSpinBox::valueChanged,
                this,
                &TaskDesignOperationTargets::applySelection
            );
        }
    }
    if (patternMode) {
        connect(patternSourceMode, &QComboBox::currentIndexChanged, this, [this]() {
            populatePatternSources();
            applySelection();
        });
        connect(
            patternSourceObject,
            &QComboBox::currentIndexChanged,
            this,
            &TaskDesignOperationTargets::applySelection
        );
        connect(
            patternOccurrences,
            &QSpinBox::valueChanged,
            this,
            &TaskDesignOperationTargets::applySelection
        );
        connect(
            patternPrimaryValue,
            &QDoubleSpinBox::valueChanged,
            this,
            &TaskDesignOperationTargets::applySelection
        );
        connect(patternOption, &QCheckBox::toggled, this, &TaskDesignOperationTargets::applySelection);
        for (auto* value : patternOriginValues) {
            connect(value, &QDoubleSpinBox::valueChanged, this, &TaskDesignOperationTargets::applySelection);
        }
        for (auto* value : patternDirectionValues) {
            connect(value, &QDoubleSpinBox::valueChanged, this, &TaskDesignOperationTargets::applySelection);
        }
        connect(
            usePatternReference,
            &QPushButton::clicked,
            this,
            &TaskDesignOperationTargets::useSelectedPatternReference
        );
        connect(
            clearPatternReferenceButton,
            &QPushButton::clicked,
            this,
            &TaskDesignOperationTargets::clearPatternReference
        );
    }
    if (splitMode) {
        connect(
            addSplitDefinitions,
            &QPushButton::clicked,
            this,
            &TaskDesignOperationTargets::addSelectedSplitDefinitions
        );
        connect(
            removeSplitDefinitions,
            &QPushButton::clicked,
            this,
            &TaskDesignOperationTargets::removeSelectedSplitDefinitions
        );
        connect(targetBodies, &QListWidget::itemSelectionChanged, this, [this]() {
            removeSplitDefinitions->setEnabled(!targetBodies->selectedItems().empty());
        });
    }

    configureOperation();
    if (fixedModifyMode) {
        // The dress-up parameter panel already presents every Body-qualified
        // edge. Keep this lifecycle controller invisible instead of showing a
        // second, stale target list.
        setVisible(false);
    }
}

TaskDesignOperationTargets::~TaskDesignOperationTargets() = default;

PartDesign::DesignOperationProperties* TaskDesignOperationTargets::operationProperties() const
{
    return dynamic_cast<PartDesign::DesignOperationProperties*>(operation);
}

void TaskDesignOperationTargets::populatePatternParameters()
{
    auto* pattern = dynamic_cast<PartDesign::DesignPatternProperties*>(operation);
    if (!pattern) {
        return;
    }

    patternSourceMode->clear();
    patternSourceMode->addItem(tr("Feature"), QStringLiteral("Feature"));
    patternSourceMode->addItem(tr("Body"), QStringLiteral("Body"));
    const int sourceIndex = patternSourceMode->findData(
        QString::fromUtf8(pattern->PatternSource.getValueAsString())
    );
    patternSourceMode->setCurrentIndex(sourceIndex >= 0 ? sourceIndex : 0);

    const auto setVector = [](const std::vector<QDoubleSpinBox*>& editors,
                              const Base::Vector3d& value) {
        if (editors.size() != 3) {
            return;
        }
        editors[0]->setValue(value.x);
        editors[1]->setValue(value.y);
        editors[2]->setValue(value.z);
    };

    patternOccurrences->setVisible(true);
    patternOccurrencesLabel->setVisible(true);
    patternPrimaryLabel->setVisible(true);
    patternPrimaryValue->setVisible(true);
    patternOriginLabel->setVisible(true);
    patternOriginEditor->setVisible(true);
    patternDirectionLabel->setVisible(true);
    patternDirectionEditor->setVisible(true);
    patternOption->setVisible(true);

    if (auto* mirror = freecad_cast<PartDesign::DesignMirror*>(operation)) {
        patternOccurrences->setVisible(false);
        patternOccurrencesLabel->setVisible(false);
        patternPrimaryLabel->setVisible(false);
        patternPrimaryValue->setVisible(false);
        patternOriginLabel->setText(tr("Plane origin"));
        patternDirectionLabel->setText(tr("Plane normal"));
        patternOption->setVisible(false);
        setVector(patternOriginValues, mirror->PlaneOrigin.getValue());
        setVector(patternDirectionValues, mirror->PlaneNormal.getValue());
    }
    else if (auto* linear = freecad_cast<PartDesign::DesignLinearPattern*>(operation)) {
        patternPrimaryLabel->setText(tr("Spacing"));
        patternPrimaryValue->setSuffix(tr(" mm"));
        patternPrimaryValue->setMaximum(1.0e9);
        patternPrimaryValue->setValue(linear->Spacing.getValue());
        patternOccurrences->setValue(linear->Occurrences.getValue());
        patternOriginLabel->setVisible(false);
        patternOriginEditor->setVisible(false);
        patternDirectionLabel->setText(tr("Direction"));
        patternOption->setText(tr("Centered"));
        patternOption->setChecked(linear->Centered.getValue());
        setVector(patternDirectionValues, linear->Direction.getValue());
    }
    else if (auto* circular = freecad_cast<PartDesign::DesignCircularPattern*>(operation)) {
        patternPrimaryLabel->setText(tr("Angle"));
        patternPrimaryValue->setSuffix(QString::fromUtf8("°"));
        patternPrimaryValue->setMaximum(360.0);
        patternPrimaryValue->setValue(circular->Angle.getValue());
        patternOccurrences->setValue(circular->Occurrences.getValue());
        patternOriginLabel->setText(tr("Axis origin"));
        patternDirectionLabel->setText(tr("Axis direction"));
        patternOption->setText(tr("Reversed"));
        patternOption->setChecked(circular->Reversed.getValue());
        setVector(patternOriginValues, circular->AxisOrigin.getValue());
        setVector(patternDirectionValues, circular->AxisDirection.getValue());
    }
}

void TaskDesignOperationTargets::populateScaleParameters()
{
    auto* scale = freecad_cast<PartDesign::DesignScale*>(operation);
    if (!scale || scaleCenterValues.size() != 3) {
        return;
    }

    scaleUniform->setChecked(scale->Uniform.getValue());
    scaleUniformFactor->setValue(scale->UniformScale.getValue());
    scaleXFactor->setValue(scale->XScale.getValue());
    scaleYFactor->setValue(scale->YScale.getValue());
    scaleZFactor->setValue(scale->ZScale.getValue());
    const Base::Vector3d center = scale->Center.getValue();
    scaleCenterValues[0]->setValue(center.x);
    scaleCenterValues[1]->setValue(center.y);
    scaleCenterValues[2]->setValue(center.z);

    const bool uniform = scaleUniform->isChecked();
    scaleUniformFactor->setEnabled(uniform);
    scaleXFactor->setEnabled(!uniform);
    scaleYFactor->setEnabled(!uniform);
    scaleZFactor->setEnabled(!uniform);
}

void TaskDesignOperationTargets::populatePatternSources()
{
    if (!patternMode || !operation || !operation->getDocument()) {
        return;
    }
    const QSignalBlocker blocker(patternSourceObject);
    const QString previous = patternSourceObject->currentData().toString();
    patternSourceObject->clear();

    auto* pattern = dynamic_cast<PartDesign::DesignPatternProperties*>(operation);
    const bool featureMode = patternSourceMode->currentData().toString() == QStringLiteral("Feature");
    QString desired = previous;
    if (featureMode && pattern && pattern->SourceOperation.getValue()) {
        desired = QString::fromLatin1(pattern->SourceOperation.getValue()->getNameInDocument());
    }
    else if (!featureMode) {
        const auto inputIds = operationProperties()->InputBodyIds.getValues();
        if (inputIds.size() == 1) {
            desired = QString::fromStdString(inputIds.front());
        }
    }

    if (featureMode) {
        auto* timeline = App::DocumentTimeline::get(operation->getDocument());
        if (timeline) {
            for (auto* candidate : timeline->Operations.getValues()) {
                if (candidate == operation) {
                    break;
                }
                auto* candidateProperties = dynamic_cast<PartDesign::DesignOperationProperties*>(
                    candidate
                );
                auto* candidateFeature = freecad_cast<PartDesign::FeatureAddSub*>(candidate);
                if (!candidateProperties || !candidateFeature) {
                    continue;
                }
                const std::string_view mode = candidateProperties->ResultOperation.getValueAsString();
                if (mode != "New Body" && mode != "Join" && mode != "Cut") {
                    continue;
                }
                patternSourceObject->addItem(
                    QString::fromUtf8(candidate->Label.getValue()),
                    QString::fromLatin1(candidate->getNameInDocument())
                );
            }
        }
    }
    else {
        for (auto* body : bodies) {
            if (!body
                || !PartDesign::designBodyStateBefore(
                    body,
                    edit.provisionalOperation ? nullptr : operation
                )) {
                continue;
            }
            patternSourceObject->addItem(
                bodyDisplayName(*body),
                QString::fromStdString(body->VibeCADBodyId.getValueStr())
            );
        }
    }

    const int desiredIndex = patternSourceObject->findData(desired);
    patternSourceObject->setCurrentIndex(
        desiredIndex >= 0 ? desiredIndex : (patternSourceObject->count() ? 0 : -1)
    );
    targetBodies->setEnabled(featureMode);
}

void TaskDesignOperationTargets::populate()
{
    populating = true;
    const QSignalBlocker modeBlocker(resultMode);
    const QSignalBlocker componentBlocker(destinationComponent);
    const QSignalBlocker resultBodyBlocker(resultBody);
    const QSignalBlocker retainedRegionBlocker(retainedRegion);
    const QSignalBlocker keepToolsBlocker(keepTools);
    const QSignalBlocker bodyBlocker(targetBodies);

    auto* properties = operationProperties();
    auto* document = operation->getDocument();
    if (patternMode) {
        const QString mode = QString::fromUtf8(properties->ResultOperation.getValueAsString());
        resultMode->addItem(mode, mode);
    }
    else if (fixedResultMode) {
        const QString mode = QString::fromUtf8(properties->ResultOperation.getValueAsString());
        resultMode->addItem(mode, mode);
    }
    else if (fixedModifyMode) {
        resultMode->addItem(tr("Modify"), QStringLiteral("Modify"));
    }
    else if (combineMode) {
        resultMode->addItem(tr("Join"), QStringLiteral("Join"));
        resultMode->addItem(tr("Cut"), QStringLiteral("Cut"));
        resultMode->addItem(tr("Intersect"), QStringLiteral("Intersect"));
    }
    else if (splitMode) {
        resultMode->addItem(tr("Split"), QStringLiteral("Split"));
    }
    else {
        resultMode->addItem(tr("New Body"), QStringLiteral("New Body"));
        resultMode->addItem(tr("Join"), QStringLiteral("Join"));
        resultMode->addItem(tr("Cut"), QStringLiteral("Cut"));
        resultMode->addItem(tr("Intersect"), QStringLiteral("Intersect"));
    }
    const int modeIndex = resultMode->findData(QString::fromUtf8(edit.originalResultMode.c_str()));
    resultMode->setCurrentIndex(modeIndex >= 0 ? modeIndex : 0);

    if (!combineMode && !splitMode) {
        components.push_back(nullptr);
        destinationComponent->addItem(tr("Design root"));
        for (auto* component : document->getObjectsOfType<App::Part>()) {
            if (!component || component->Type.getStrValue() != "Component") {
                continue;
            }
            components.push_back(component);
            destinationComponent->addItem(QString::fromUtf8(component->Label.getValue()));
        }
        std::string destinationId;
        const auto outputComponents = properties->OutputComponentIds.getValues();
        const auto previousInputIndices = properties->OutputPreviousInputIndices.getValues();
        if (separateMode && !outputComponents.empty()
            && std::ranges::all_of(outputComponents, [&outputComponents](const std::string& componentId) {
                   return componentId == outputComponents.front();
               })) {
            destinationId = outputComponents.front();
        }
        else if (outputComponents.size() == 1 && previousInputIndices.size() == 1
                 && previousInputIndices.front() == -1) {
            destinationId = outputComponents.front();
        }
        for (std::size_t index = 1; index < components.size(); ++index) {
            if (PartDesign::DesignModel::componentId(*components[index]) == destinationId) {
                destinationComponent->setCurrentIndex(static_cast<int>(index));
                break;
            }
        }
    }

    bodies = document->getObjectsOfType<PartDesign::Body>();
    std::ranges::sort(bodies, [](const auto* left, const auto* right) {
        return left->getID() < right->getID();
    });
    if (patternMode) {
        populatePatternParameters();
        populatePatternSources();
    }
    if (scaleMode) {
        populateScaleParameters();
    }

    std::string combineResultId;
    std::unordered_set<std::string> selectedIds;
    if (auto* combine = freecad_cast<PartDesign::DesignCombine*>(operation)) {
        combineResultId = combine->ResultBodyId.getValueStr();
        keepTools->setChecked(combine->KeepTools.getValue());
        const auto inputIds = combine->InputBodyIds.getValues();
        selectedIds.insert(inputIds.begin(), inputIds.end());
    }
    else if (auto* split = freecad_cast<PartDesign::DesignSplit*>(operation)) {
        combineResultId = split->SourceBodyId.getValueStr();
    }
    else if (!patternMode
             || patternSourceMode->currentData().toString() == QStringLiteral("Feature")) {
        const auto targetIds = properties->OutputBodyIds.getValues();
        selectedIds.insert(targetIds.begin(), targetIds.end());
    }

    if (separateMode) {
        populateSeparateRows();
    }
    else {
        for (auto* body : bodies) {
            const std::string bodyId = body->VibeCADBodyId.getValueStr();
            if ((combineMode || splitMode)
                && PartDesign::designBodyStateBefore(
                    body,
                    edit.provisionalOperation ? nullptr : operation
                )) {
                resultBody->addItem(bodyDisplayName(*body), QString::fromStdString(bodyId));
            }

            if (splitMode) {
                continue;
            }

            auto* item = new QListWidgetItem(bodyDisplayName(*body), targetBodies);
            if (!fixedModifyMode) {
                item->setFlags(item->flags() | Qt::ItemIsUserCheckable);
            }
            item->setCheckState(selectedIds.contains(bodyId) ? Qt::Checked : Qt::Unchecked);
            if (!PartDesign::designBodyStateBefore(
                    body,
                    edit.provisionalOperation ? nullptr : operation
                )) {
                item->setFlags(item->flags() & ~Qt::ItemIsEnabled);
                item->setToolTip(tr("This Body has no solid state at this History position"));
            }
        }
    }

    if (combineMode || splitMode) {
        const int resultIndex = resultBody->findData(QString::fromStdString(combineResultId));
        resultBody->setCurrentIndex(resultIndex >= 0 ? resultIndex : 0);
    }
    if (combineMode) {
        updateCombineBodyRows();
    }
    if (splitMode) {
        auto* split = freecad_cast<PartDesign::DesignSplit*>(operation);
        populateSplitRows(split->Splitters.getSubListValues());
    }
    if (fixedModifyMode) {
        targetBodies->setEnabled(false);
    }
    if (separateMode) {
        targetBodies->setEnabled(false);
    }
    if (patternMode) {
        targetBodies->setEnabled(
            patternSourceMode->currentData().toString() == QStringLiteral("Feature")
        );
        updatePatternReferenceLabel();
    }
    populating = false;
}

void TaskDesignOperationTargets::populateSeparateRows()
{
    if (!separateMode || !operation || !operation->getDocument()) {
        return;
    }

    const QSignalBlocker blocker(targetBodies);
    targetBodies->clear();

    std::unordered_set<std::string> originalIds;
    for (const auto* state : edit.originalStates) {
        if (state) {
            originalIds.insert(state->BodyId.getValueStr());
        }
    }

    const auto outputIds = operationProperties()->OutputBodyIds.getValues();
    std::size_t retained = 0;
    std::size_t added = 0;
    for (std::size_t index = 0; index < outputIds.size(); ++index) {
        auto* body = PartDesign::DesignModel::bodyWithId(*operation->getDocument(), outputIds[index]);
        const bool existingIdentity = originalIds.contains(outputIds[index]);
        existingIdentity ? ++retained : ++added;

        const QString label = body ? bodyDisplayName(*body)
                                   : tr("New Body %1").arg(static_cast<int>(index + 1));
        auto* item = new QListWidgetItem(label, targetBodies);
        item->setCheckState(Qt::Checked);
        item->setFlags(item->flags() & ~Qt::ItemIsUserCheckable);
        item->setToolTip(
            existingIdentity ? tr("This Body identity will be preserved")
                             : tr("This solid will receive a new Body identity")
        );
    }

    std::size_t removed = originalIds.size();
    for (const auto& outputId : outputIds) {
        removed -= originalIds.contains(outputId) ? 1 : 0;
    }
    if (added == 0 && removed == 0) {
        separateSummary->setText(tr("%n Body identity preserved", nullptr, static_cast<int>(retained))
        );
    }
    else {
        separateSummary->setText(
            tr("%1 preserved · %2 new · %3 removed on Accept").arg(retained).arg(added).arg(removed)
        );
    }
}

void TaskDesignOperationTargets::populateSplitRows(
    const std::vector<App::PropertyLinkSubList::SubSet>& references
)
{
    auto* split = freecad_cast<PartDesign::DesignSplit*>(operation);
    auto* source = selectedResultBody();
    if (!split || !source) {
        throw Base::ValueError("Split requires one explicit source Body");
    }

    splitWitnesses = PartDesign::DesignModel::setSplitDefinition(edit, *source, references);

    const QSignalBlocker bodyBlocker(targetBodies);
    const QSignalBlocker retainedRegionBlocker(retainedRegion);
    targetBodies->clear();
    const auto normalized = split->Splitters.getSubListValues();
    for (const auto& reference : normalized) {
        QString label = reference.first ? QString::fromUtf8(reference.first->Label.getValue())
                                        : tr("Missing definition");
        if (!reference.second.empty()
            && !(reference.second.size() == 1 && reference.second.front().empty())) {
            QStringList subelements;
            for (const auto& subelement : reference.second) {
                subelements.push_back(QString::fromStdString(subelement));
            }
            label = QStringLiteral("%1 — %2").arg(label, subelements.join(QStringLiteral(", ")));
        }
        auto* item = new QListWidgetItem(label, targetBodies);
        item->setFlags(item->flags() & ~Qt::ItemIsUserCheckable);
    }

    retainedRegion->clear();
    retainedRegion->addItem(tr("Choose a region…"), -1);
    for (std::size_t index = 0; index < splitWitnesses.size(); ++index) {
        const auto& witness = splitWitnesses[index];
        retainedRegion->addItem(
            tr("Region %1  (%2, %3, %4)")
                .arg(index + 1)
                .arg(witness.x, 0, 'g', 5)
                .arg(witness.y, 0, 'g', 5)
                .arg(witness.z, 0, 'g', 5),
            static_cast<int>(index)
        );
    }
    retainedRegion->setCurrentIndex(split->RetainedRegionChosen.getValue() ? 1 : 0);
    removeSplitDefinitions->setEnabled(false);
}

std::vector<PartDesign::Body*> TaskDesignOperationTargets::selectedBodies() const
{
    std::vector<PartDesign::Body*> result;
    for (int index = 0; index < targetBodies->count(); ++index) {
        const auto* item = targetBodies->item(index);
        if (item && item->checkState() == Qt::Checked && item->flags().testFlag(Qt::ItemIsEnabled)) {
            result.push_back(bodies.at(static_cast<std::size_t>(index)));
        }
    }
    return result;
}

std::vector<PartDesign::Body*> TaskDesignOperationTargets::selectedToolBodies() const
{
    std::vector<PartDesign::Body*> result;
    auto* selectedResult = selectedResultBody();
    for (int index = 0; index < targetBodies->count(); ++index) {
        const auto* item = targetBodies->item(index);
        auto* body = bodies.at(static_cast<std::size_t>(index));
        if (item && body != selectedResult && item->checkState() == Qt::Checked
            && item->flags().testFlag(Qt::ItemIsEnabled)) {
            result.push_back(body);
        }
    }
    return result;
}

PartDesign::Body* TaskDesignOperationTargets::selectedResultBody() const
{
    const std::string bodyId = resultBody->currentData().toString().toStdString();
    const auto found = std::ranges::find(bodies, bodyId, [](const PartDesign::Body* body) {
        return body ? body->VibeCADBodyId.getValueStr() : std::string();
    });
    return found != bodies.end() ? *found : nullptr;
}

PartDesign::Body* TaskDesignOperationTargets::selectedPatternSourceBody() const
{
    if (!patternMode || patternSourceMode->currentData().toString() != QStringLiteral("Body")) {
        return nullptr;
    }
    const std::string bodyId = patternSourceObject->currentData().toString().toStdString();
    const auto found = std::ranges::find(bodies, bodyId, [](const PartDesign::Body* body) {
        return body ? body->VibeCADBodyId.getValueStr() : std::string();
    });
    return found != bodies.end() ? *found : nullptr;
}

App::DocumentObject* TaskDesignOperationTargets::selectedPatternSourceOperation() const
{
    if (!patternMode || !operation || !operation->getDocument()
        || patternSourceMode->currentData().toString() != QStringLiteral("Feature")) {
        return nullptr;
    }
    return operation->getDocument()->getObject(
        patternSourceObject->currentData().toString().toLatin1().constData()
    );
}

App::Part* TaskDesignOperationTargets::selectedDestinationComponent() const
{
    const int index = destinationComponent->currentIndex();
    return index >= 0 && static_cast<std::size_t>(index) < components.size()
        ? components[static_cast<std::size_t>(index)]
        : nullptr;
}

std::size_t TaskDesignOperationTargets::generatedPatternCopyCount() const
{
    if (freecad_cast<PartDesign::DesignMirror*>(operation)) {
        return 1;
    }
    if (freecad_cast<PartDesign::DesignLinearPattern*>(operation)
        || freecad_cast<PartDesign::DesignCircularPattern*>(operation)) {
        return static_cast<std::size_t>(std::max(0, patternOccurrences->value() - 1));
    }
    return 0;
}

void TaskDesignOperationTargets::updateCombineBodyRows()
{
    if (!combineMode) {
        return;
    }

    const QSignalBlocker bodyBlocker(targetBodies);
    auto* selectedResult = selectedResultBody();
    for (int index = 0; index < targetBodies->count(); ++index) {
        auto* item = targetBodies->item(index);
        auto* body = bodies.at(static_cast<std::size_t>(index));
        if (!item) {
            continue;
        }

        const bool hasState
            = PartDesign::designBodyStateBefore(body, edit.provisionalOperation ? nullptr : operation)
            != nullptr;
        const bool isResult = body == selectedResult;
        item->setFlags(
            hasState && !isResult ? item->flags() | Qt::ItemIsEnabled
                                  : item->flags() & ~Qt::ItemIsEnabled
        );
        if (isResult) {
            item->setCheckState(Qt::Unchecked);
            item->setToolTip(tr("This Body receives the combined result"));
        }
        else if (!hasState) {
            item->setCheckState(Qt::Unchecked);
            item->setToolTip(tr("This Body has no solid state at this History position"));
        }
        else {
            item->setToolTip({});
        }
    }
}

void TaskDesignOperationTargets::configurePattern()
{
    if (!patternMode) {
        return;
    }
    const auto vectorValue = [](const std::vector<QDoubleSpinBox*>& values) {
        return values.size() == 3
            ? Base::Vector3d(values[0]->value(), values[1]->value(), values[2]->value())
            : Base::Vector3d();
    };

    if (auto* mirror = freecad_cast<PartDesign::DesignMirror*>(operation)) {
        mirror->PlaneOrigin.setValue(vectorValue(patternOriginValues));
        mirror->PlaneNormal.setValue(vectorValue(patternDirectionValues));
    }
    else if (auto* linear = freecad_cast<PartDesign::DesignLinearPattern*>(operation)) {
        linear->Direction.setValue(vectorValue(patternDirectionValues));
        linear->Spacing.setValue(patternPrimaryValue->value());
        linear->Occurrences.setValue(patternOccurrences->value());
        linear->Centered.setValue(patternOption->isChecked());
    }
    else if (auto* circular = freecad_cast<PartDesign::DesignCircularPattern*>(operation)) {
        circular->AxisOrigin.setValue(vectorValue(patternOriginValues));
        circular->AxisDirection.setValue(vectorValue(patternDirectionValues));
        circular->Angle.setValue(patternPrimaryValue->value());
        circular->Occurrences.setValue(patternOccurrences->value());
        circular->Reversed.setValue(patternOption->isChecked());
    }
    else {
        throw Base::TypeError("The task panel received an unsupported Design Pattern");
    }

    const bool featureMode = patternSourceMode->currentData().toString() == QStringLiteral("Feature");
    targetBodies->setEnabled(featureMode);
    if (featureMode) {
        auto* source = selectedPatternSourceOperation();
        if (!source) {
            throw Base::ValueError("Choose one earlier additive or subtractive Design feature");
        }
        PartDesign::DesignModel::setFeaturePatternTargets(edit, *source, selectedBodies(), true);
    }
    else {
        auto* source = selectedPatternSourceBody();
        if (!source) {
            throw Base::ValueError("Choose one Body with a solid at this History position");
        }
        PartDesign::DesignModel::setBodyPatternSource(edit, *source, generatedPatternCopyCount());
    }

    if (auto* feature = freecad_cast<PartDesign::Feature*>(operation)) {
        feature->recomputeFeature();
        feature->recomputePreview();
    }
    updatePatternReferenceLabel();
}

void TaskDesignOperationTargets::configureOperation()
{
    if (populating || fixedModifyMode) {
        return;
    }

    if (patternMode) {
        configurePattern();
        return;
    }
    if (scaleMode) {
        configureScale();
        return;
    }

    if (splitMode) {
        auto* source = selectedResultBody();
        const int retained = retainedRegion->currentData().toInt();
        if (!source) {
            throw Base::ValueError("Split requires one explicit source Body");
        }
        if (retained >= 0) {
            PartDesign::DesignModel::assignSplitRegions(
                edit,
                *source,
                splitWitnesses,
                static_cast<std::size_t>(retained)
            );
            if (auto* feature = freecad_cast<PartDesign::Feature*>(operation)) {
                feature->recomputeFeature();
                feature->recomputePreview();
            }
        }
        return;
    }

    if (separateMode) {
        auto* separate = freecad_cast<PartDesign::DesignSeparate*>(operation);
        auto* source = separate ? separate->Source.getValue() : nullptr;
        if (!separate || !source) {
            throw Base::ValueError("Separate requires one reusable multi-solid definition");
        }
        PartDesign::DesignModel::setSeparateDefinition(edit, *source, selectedDestinationComponent());
        separate->recomputeFeature();
        separate->recomputePreview();
        populateSeparateRows();
        return;
    }

    const std::string mode = resultMode->currentData().toString().toStdString();

    if (combineMode) {
        updateCombineBodyRows();
        auto* result = selectedResultBody();
        if (!result) {
            throw Base::ValueError("Combine requires one explicit result Body");
        }
        PartDesign::DesignModel::setCombineBodies(
            edit,
            mode,
            *result,
            selectedToolBodies(),
            keepTools->isChecked(),
            true
        );

        if (auto* feature = freecad_cast<PartDesign::Feature*>(operation)) {
            feature->recomputeFeature();
            feature->recomputePreview();
        }
        return;
    }

    const bool newBody = mode == "New Body";
    targetBodies->setEnabled(!newBody);
    destinationComponent->setEnabled(newBody);

    PartDesign::DesignModel::setOperationTargets(
        edit,
        mode,
        newBody ? std::vector<PartDesign::Body*> {} : selectedBodies(),
        newBody ? selectedDestinationComponent() : nullptr,
        true
    );

    if (auto* feature = freecad_cast<PartDesign::Feature*>(operation)) {
        feature->recomputeFeature();
        feature->recomputePreview();
    }
}

void TaskDesignOperationTargets::configureScale()
{
    auto* scale = freecad_cast<PartDesign::DesignScale*>(operation);
    if (!scale || scaleCenterValues.size() != 3) {
        throw Base::TypeError("Scale task lost its Design Scale operation");
    }

    scale->Uniform.setValue(scaleUniform->isChecked());
    scale->UniformScale.setValue(scaleUniformFactor->value());
    scale->XScale.setValue(scaleXFactor->value());
    scale->YScale.setValue(scaleYFactor->value());
    scale->ZScale.setValue(scaleZFactor->value());
    scale->Center.setValue(Base::Vector3d(
        scaleCenterValues[0]->value(),
        scaleCenterValues[1]->value(),
        scaleCenterValues[2]->value()
    ));

    PartDesign::DesignModel::setOperationTargets(
        edit,
        "Modify",
        selectedBodies(),
        nullptr,
        true
    );
    scale->recomputeFeature();
    scale->recomputePreview();
}

void TaskDesignOperationTargets::addSelectedSplitDefinitions()
{
    auto* split = freecad_cast<PartDesign::DesignSplit*>(operation);
    auto* source = selectedResultBody();
    if (!split || !source || !operation->getDocument()) {
        return;
    }

    try {
        auto references = split->Splitters.getSubListValues();
        bool changed = false;
        for (auto& selected : Gui::Selection().getSelectionEx()) {
            auto* object = selected.getObject();
            if (!object || object->getDocument() != operation->getDocument()) {
                throw Base::ValueError("Select Split definitions from this document");
            }
            if (!PartGui::isModelingObjectActive(object)) {
                throw Base::ValueError("A selected Split definition is not active at the "
                                       "current History position");
            }
            if (object == operation
                || (!freecad_cast<PartDesign::Body*>(object) && !freecad_cast<Part::Feature*>(object)
                    && !freecad_cast<PartDesign::DesignBodyPublication*>(object))) {
                throw Base::TypeError("Select a face, surface, shell, solid, or Body");
            }

            const auto& subElements = selected.getSubNames();
            auto found = std::ranges::find(references, object, [](const auto& reference) {
                return reference.first;
            });
            if (found == references.end()) {
                references.emplace_back(object, subElements);
                changed = true;
                continue;
            }

            if (found->second.empty()) {
                continue;
            }
            if (subElements.empty()) {
                found->second.clear();
                changed = true;
                continue;
            }
            for (const auto& subElement : subElements) {
                if (std::ranges::find(found->second, subElement) == found->second.end()) {
                    found->second.push_back(subElement);
                    changed = true;
                }
            }
        }

        if (!changed) {
            throw Base::ValueError("Select at least one new Split definition in the 3D view "
                                   "or tree");
        }
        populateSplitRows(references);
        Gui::Selection().clearSelection(operation->getDocument()->getName());
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("Cannot add Split definition"),
            QString::fromUtf8(error.what())
        );
    }
    catch (const Standard_Failure& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("Cannot add Split definition"),
            QString::fromUtf8(error.GetMessageString())
        );
    }
}

void TaskDesignOperationTargets::removeSelectedSplitDefinitions()
{
    auto* split = freecad_cast<PartDesign::DesignSplit*>(operation);
    if (!split) {
        return;
    }

    try {
        auto references = split->Splitters.getSubListValues();
        std::vector<bool> removed(references.size(), false);
        for (auto* item : targetBodies->selectedItems()) {
            const int row = targetBodies->row(item);
            if (row >= 0 && static_cast<std::size_t>(row) < removed.size()) {
                removed[static_cast<std::size_t>(row)] = true;
            }
        }

        std::vector<App::PropertyLinkSubList::SubSet> retained;
        retained.reserve(references.size());
        for (std::size_t index = 0; index < references.size(); ++index) {
            if (!removed[index]) {
                retained.push_back(std::move(references[index]));
            }
        }
        if (retained.size() == references.size()) {
            return;
        }
        if (retained.empty()) {
            throw Base::ValueError("Split requires at least one splitting definition");
        }
        populateSplitRows(retained);
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("Cannot remove Split definition"),
            QString::fromUtf8(error.what())
        );
    }
    catch (const Standard_Failure& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("Cannot remove Split definition"),
            QString::fromUtf8(error.GetMessageString())
        );
    }
}

void TaskDesignOperationTargets::updatePatternReferenceLabel()
{
    if (!patternMode) {
        return;
    }
    const App::PropertyLinkSub* reference = nullptr;
    if (const auto* mirror = freecad_cast<const PartDesign::DesignMirror*>(operation)) {
        reference = &mirror->PlaneReference;
    }
    else if (const auto* linear = freecad_cast<const PartDesign::DesignLinearPattern*>(operation)) {
        reference = &linear->DirectionReference;
    }
    else if (const auto* circular = freecad_cast<const PartDesign::DesignCircularPattern*>(operation)) {
        reference = &circular->AxisReference;
    }

    auto* object = reference ? reference->getValue() : nullptr;
    if (!object) {
        patternReference->setText(tr("Using the numeric definition"));
        clearPatternReferenceButton->setEnabled(false);
        return;
    }
    const auto subelements = reference->getSubValues();
    QString text = QString::fromUtf8(object->Label.getValue());
    if (!subelements.empty() && !subelements.front().empty()) {
        text += QStringLiteral(" — ") + QString::fromStdString(subelements.front());
    }
    patternReference->setText(text);
    clearPatternReferenceButton->setEnabled(true);
}

void TaskDesignOperationTargets::useSelectedPatternReference()
{
    if (!patternMode || !operation || !operation->getDocument()) {
        return;
    }
    try {
        const auto selected = PartGui::getModelingSelection(operation->getDocument()->getName());
        if (selected.size() != 1) {
            throw Base::ValueError("Select exactly one datum, sketch axis, edge, or face");
        }
        auto* referenceObject = PartDesignGui::resolveModelingReference(
            operation,
            const_cast<App::DocumentObject*>(selected.front().getObject())
        );
        const auto& subelements = selected.front().getSubNames();
        if (!referenceObject || referenceObject->getDocument() != operation->getDocument()
            || subelements.size() > 1) {
            throw Base::ValueError("The selected reference is not one exact modeling definition");
        }
        const std::vector<std::string> oneSubelement = subelements.empty()
            ? std::vector<std::string> {}
            : std::vector<std::string> {
                  subelements.front(),
              };
        auto exact =
            PartDesign::DesignModel::resolveDefinitionSubelementReference(
                *operation,
                *referenceObject,
                oneSubelement
            );
        referenceObject = exact.object;

        if (auto* mirror = freecad_cast<PartDesign::DesignMirror*>(operation)) {
            mirror->PlaneReference.setValue(
                referenceObject,
                exact.subelements
            );
        }
        else if (auto* linear = freecad_cast<PartDesign::DesignLinearPattern*>(operation)) {
            linear->DirectionReference.setValue(
                referenceObject,
                exact.subelements
            );
        }
        else if (auto* circular = freecad_cast<PartDesign::DesignCircularPattern*>(operation)) {
            circular->AxisReference.setValue(
                referenceObject,
                exact.subelements
            );
        }
        else {
            throw Base::TypeError("The active operation has no Pattern reference");
        }
        configurePattern();
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("Cannot use Pattern reference"),
            QString::fromUtf8(error.what())
        );
    }
    catch (const Standard_Failure& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("Cannot use Pattern reference"),
            QString::fromUtf8(error.GetMessageString())
        );
    }
}

void TaskDesignOperationTargets::clearPatternReference()
{
    if (auto* mirror = freecad_cast<PartDesign::DesignMirror*>(operation)) {
        mirror->PlaneReference.setValue(nullptr, {});
    }
    else if (auto* linear = freecad_cast<PartDesign::DesignLinearPattern*>(operation)) {
        linear->DirectionReference.setValue(nullptr, {});
    }
    else if (auto* circular = freecad_cast<PartDesign::DesignCircularPattern*>(operation)) {
        circular->AxisReference.setValue(nullptr, {});
    }
    configurePattern();
}

void TaskDesignOperationTargets::applySelection()
{
    configureOperation();
}

void TaskDesignOperationTargets::finalize()
{
    if (!fixedModifyMode) {
        configureOperation();
    }
    const auto originalBodyIds = [&]() {
        std::unordered_set<std::string> result;
        for (const auto* state : edit.originalStates) {
            if (state) {
                result.insert(state->BodyId.getValueStr());
            }
        }
        return result;
    }();
    const auto outputs = PartDesign::DesignModel::finalizeOperation(edit);
    if (!separateMode) {
        return;
    }

    const auto* separate = freecad_cast<const PartDesign::DesignSeparate*>(operation);
    const auto* source = separate ? separate->Source.getValue() : nullptr;
    if (!source) {
        throw Base::RuntimeError("Separate lost its reusable source while publishing appearance");
    }
    for (std::size_t index = 0; index < outputs.size(); ++index) {
        auto* output = outputs[index];
        if (output && !originalBodyIds.contains(output->VibeCADBodyId.getValueStr())) {
            output->Label.setValue(
                (std::string(source->Label.getValue()) + " " + std::to_string(index + 1)).c_str()
            );
            PartDesignGui::copyShapeVisualProperties(*output, *source);
        }
    }
}

#include "moc_TaskDesignOperation.cpp"
