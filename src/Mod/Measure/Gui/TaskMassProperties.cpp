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

#include "TaskMassProperties.h"
#include "Mod/Measure/App/MassPropertiesOccurrence.h"
#include "Mod/Measure/App/MassPropertiesResult.h"
#include "Mod/Measure/App/MassPropertiesObject.h"
#include "TimelineSelection.h"
#include "ViewProviderMassPropertiesResult.h"
#include "ui_TaskMassProperties.h"

#include <QtCore/QScopedValueRollback>
#include <QKeyEvent>
#include <QTimer>

#include <QtWidgets>
#include <algorithm>
#include <unordered_set>
#include <sstream>
#include <tuple>
#include <vector>

#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/Control.h>
#include <Gui/Selection/Selection.h>
#include <Gui/Application.h>
#include <Gui/ExactTransaction.h>
#include <Gui/Document.h>
#include <Gui/ViewProvider.h>
#include <Gui/ViewProviderDocumentObject.h>

#include <Base/Console.h>
#include <Base/Exception.h>
#include <Base/Matrix.h>
#include <Base/Parameter.h>
#include <Base/Placement.h>
#include <Base/Precision.h>
#include <Base/Quantity.h>
#include <Base/Rotation.h>
#include <Base/Type.h>
#include <Base/UnitsApi.h>
#include <Base/Vector3D.h>


#include <App/Application.h>
#include <App/DocumentObject.h>
#include <App/Document.h>
#include <App/Datums.h>
#include <App/Origin.h>
#include <App/GroupExtension.h>
#include <App/Link.h>
#include <App/DocumentObjectGroup.h>
#include <App/DocumentObserver.h>
#include <App/GeoFeature.h>
#include <App/PropertyGeo.h>
#include <App/PropertyLinks.h>
#include <App/PropertyStandard.h>
#include <App/PropertyUnits.h>

#include <Mod/Part/App/PartFeature.h>

#include <TopoDS_Compound.hxx>
#include <TopoDS_Shape.hxx>
#include <BRep_Builder.hxx>

using namespace MassPropertiesGui;
using MeasureGui::isTimelineSelectionActive;

namespace
{

Gui::ViewProvider* viewProviderOf(App::DocumentObject* object)
{
    if (!object || !object->getDocument() || !Gui::Application::Instance) {
        return nullptr;
    }
    auto* guiDoc = Gui::Application::Instance->getDocument(object->getDocument());
    return guiDoc ? guiDoc->getViewProvider(object) : nullptr;
}

bool viewProviderIsShown(App::DocumentObject* object)
{
    auto* viewProvider = viewProviderOf(object);
    return viewProvider && viewProvider->isShow();
}

bool isPresentedForMassProperties(App::DocumentObject* object)
{
    // PartDesign keeps Tip features' Visibility property false while the Body
    // still displays them. Climb to a shown container instead of treating the
    // hidden property as "not in the 3D view".
    std::unordered_set<const App::DocumentObject*> seen;
    auto* current = object;
    while (current && seen.insert(current).second) {
        if (viewProviderIsShown(current)) {
            return true;
        }
        auto* group = App::GroupExtension::getGroupOfObject(current);
        if (!group || group == current) {
            break;
        }
        current = group;
    }
    return false;
}

}  // namespace

namespace MassPropertiesGui
{

class OwnedMassPropertiesTransaction
{
public:
    OwnedMassPropertiesTransaction(
        App::Document& targetDocument,
        const char* name
    )
        : transaction(targetDocument, name ? name : "")
    {}

    OwnedMassPropertiesTransaction(
        const OwnedMassPropertiesTransaction&
    ) = delete;
    OwnedMassPropertiesTransaction& operator=(
        const OwnedMassPropertiesTransaction&
    ) = delete;

    bool commit()
    {
        return transaction.commit();
    }

    bool abort()
    {
        return transaction.abort();
    }

    bool ownsCurrentTransaction() const
    {
        return transaction.ownsCurrentTransaction();
    }

private:
    Gui::ExactTransaction transaction;
};

class TaskMassPropertiesWidget: public QWidget
{
public:
    TaskMassPropertiesWidget()
        : coordinateSystemGroup(this)
    {
        ui.setupUi(this);

        coordinateSystemGroup.addButton(ui.centerOfGravityRadioButton);
        coordinateSystemGroup.addButton(ui.customRadioButton);

        shortcutQuit = new QShortcut(this);
        shortcutQuit->setKey(QKeySequence(QStringLiteral("ESC")));
        shortcutQuit->setContext(Qt::ApplicationShortcut);
    }

    QWidget* takePage(QWidget* page)
    {
        if (auto* pageLayout = page->layout()) {
            pageLayout->setContentsMargins(10, 10, 10, 10);
        }

        ui.mainLayout->removeWidget(page);
        page->setParent(nullptr);
        return page;
    }

    Ui::TaskMassProperties ui;
    QButtonGroup coordinateSystemGroup;
    QShortcut* shortcutQuit = nullptr;
};

}  // namespace MassPropertiesGui

enum UnitsComboIndex
{
    UnitsInternal = 0,
    UnitsMks = 1,
    UnitsImperial = 2,
    UnitsImperialCivil = 3
};

static int getPreferredUnitsSchemaIndex(const App::Document* document)
{
    auto params = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/Units"
    );
    const bool ignoreProjectSchema = params->GetBool("IgnoreProjectSchema", false);
    int schemaIndex = params->GetInt("UserSchema", 0);

    if (!ignoreProjectSchema && document) {
        schemaIndex = document->UnitSystem.getValue();
    }

    const int schemaCount = static_cast<int>(Base::UnitsApi::count());
    if (schemaIndex < 0 || schemaIndex >= schemaCount) {
        schemaIndex = 0;
    }

    return schemaIndex;
}

static int getUnitsComboIndex(int schemaIndex)
{
    std::string lengthUnit = "mm";

    auto schema = Base::UnitsApi::createSchema(static_cast<std::size_t>(schemaIndex));
    if (schema) {
        lengthUnit = schema->getBasicLengthUnit();
    }

    if (lengthUnit == "m") {
        return UnitsMks;
    }
    if (lengthUnit == "in") {
        return UnitsImperial;
    }
    if (lengthUnit == "ft") {
        return UnitsImperialCivil;
    }
    return UnitsInternal;
}

static int findUnitsSchemaIndex(const char* schemaName, int fallbackSchemaIndex)
{
    const auto names = Base::UnitsApi::getNames();
    for (std::size_t index = 0; index < names.size(); ++index) {
        if (names[index] == schemaName) {
            return static_cast<int>(index);
        }
    }

    return fallbackSchemaIndex;
}

static int getUnitsSchemaIndex(int comboIndex, int preferredSchemaIndex)
{
    switch (comboIndex) {
        case UnitsMks:
            return findUnitsSchemaIndex("MKS", preferredSchemaIndex);
        case UnitsImperial:
            return findUnitsSchemaIndex("Imperial", preferredSchemaIndex);
        case UnitsImperialCivil:
            return findUnitsSchemaIndex("ImperialCivil", preferredSchemaIndex);
        default:
            return findUnitsSchemaIndex("Internal", preferredSchemaIndex);
    }
}

TaskMassProperties::TaskMassProperties()
    : Gui::SelectionObserver(true, Gui::ResolveMode::NoResolve)
    , panel(new TaskMassPropertiesWidget)
    , selectingCustomCoordSystem(false)
{
    auto* document = App::GetApplication().getActiveDocument();
    if (!document) {
        throw Base::RuntimeError(
            "Mass properties requires an active document"
        );
    }
    targetDocumentName = document->getName();
    targetDocumentUid = document->Uid.getValueStr();
    targetDocumentAddress = document;
    setAutoCloseOnDeletedDocument(true);
    if (!startPreviewTransaction()) {
        throw Base::RuntimeError(
            "Could not establish the mass-properties preview transaction"
        );
    }

    currentInfo = MassPropertiesData {};
    currentMode = MassPropertiesMode::CenterOfGravity;
    clearCurrentDatumObject();
    hasCurrentDatumPlacement = false;

    qApp->installEventFilter(this);

    if (auto* app = Gui::Application::Instance) {
        if (auto* stdDeleteCommand = app->commandManager().getCommandByName("Std_Delete")) {
            if ((deleteAction = stdDeleteCommand->getAction())) {
                deleteActivated = deleteAction->isEnabled();
                deleteAction->setEnabled(false);
            }
        }
    }

    connect(panel->shortcutQuit, &QShortcut::activated, this, &TaskMassProperties::escape);
    connect(panel->ui.centerOfGravityRadioButton, &QRadioButton::toggled, this, [this](bool checked) {
        if (checked) {
            onCoordinateSystemChanged(MassPropertiesMode::CenterOfGravity);
        }
    });
    connect(panel->ui.customRadioButton, &QRadioButton::toggled, this, [this](bool checked) {
        if (checked) {
            onCoordinateSystemChanged(MassPropertiesMode::Custom);
        }
    });
    connect(
        panel->ui.selectCustomButton,
        &QPushButton::pressed,
        this,
        &TaskMassProperties::onSelectCustomCoordinateSystem
    );
    connect(
        panel->ui.cogDatumButton,
        &QPushButton::pressed,
        this,
        &TaskMassProperties::onCogDatumButtonPressed
    );
    connect(
        panel->ui.covDatumButton,
        &QPushButton::pressed,
        this,
        &TaskMassProperties::onCovDatumButtonPressed
    );
    connect(
        panel->ui.inertiaLcsButton,
        &QPushButton::pressed,
        this,
        &TaskMassProperties::onLcsButtonPressed
    );

    const int preferredSchemaIndex = getPreferredUnitsSchemaIndex(document);

    panel->ui.unitsComboBox->setCurrentIndex(getUnitsComboIndex(preferredSchemaIndex));
    unitsSchemaIndex
        = getUnitsSchemaIndex(panel->ui.unitsComboBox->currentIndex(), preferredSchemaIndex);

    connect(
        panel->ui.unitsComboBox,
        qOverload<int>(&QComboBox::currentIndexChanged),
        this,
        [this, preferredSchemaIndex](int index) {
            unitsSchemaIndex = getUnitsSchemaIndex(index, preferredSchemaIndex);
            update(Gui::SelectionChanges());
        }
    );

    auto addTaskBox = [this](const char* icon, const QString& title, QWidget* page) {
        auto* box = new Gui::TaskView::TaskBox(Gui::BitmapFactory().pixmap(icon), title, true, nullptr);
        auto* layout = box->groupLayout();
        layout->addWidget(page);
        Content.emplace_back(box);
    };

    addTaskBox("MassPropertiesIcon", tr("Parameters"), panel->takePage(panel->ui.parametersPage));
    addTaskBox(
        "MassPropertiesIcon",
        tr("Physical Properties"),
        panel->takePage(panel->ui.physicalPropertiesPage)
    );
    addTaskBox("COG-Icon", tr("Center of Gravity"), panel->takePage(panel->ui.centerOfGravityPage));
    addTaskBox("COV-Icon", tr("Center of Volume"), panel->takePage(panel->ui.centerOfVolumePage));
    addTaskBox("Std_CoordinateSystem", tr("Inertia"), panel->takePage(panel->ui.inertiaPage));

    updateInertiaVisibility();
    update(Gui::SelectionChanges());
}

TaskMassProperties::~TaskMassProperties()
{
    abortPreviewTransaction();
    qApp->removeEventFilter(this);
    if (deleteAction) {
        deleteAction->setEnabled(deleteActivated);
    }
    delete panel;
}

App::Document* TaskMassProperties::targetDocument() const
{
    if (targetDocumentName.empty()) {
        return nullptr;
    }
    try {
        auto* document = App::GetApplication().getDocument(
            targetDocumentName.c_str()
        );
        return document && document == targetDocumentAddress
                && !targetDocumentUid.empty()
                && document->Uid.getValueStr() == targetDocumentUid
            ? document
            : nullptr;
    }
    catch (...) {
        return nullptr;
    }
}

App::DocumentObject* TaskMassProperties::currentDatumObject() const
{
    if (currentDatumDocumentName.empty() || currentDatumName.empty()
        || currentDatumId < 0) {
        return nullptr;
    }

    try {
        auto* document = App::GetApplication().getDocument(
            currentDatumDocumentName.c_str()
        );
        if (!document || document != currentDatumDocumentAddress
            || currentDatumDocumentUid.empty()
            || document->Uid.getValueStr() != currentDatumDocumentUid) {
            return nullptr;
        }
        auto* object = document->getObjectByID(currentDatumId);
        if (!object || object->getDocument() != targetDocument()
            || currentDatumName != object->getNameInDocument()
            || !document->containsObject(object)
            || document->getObject(currentDatumName.c_str()) != object
            || !isTimelineSelectionActive(object)) {
            return nullptr;
        }
        return object;
    }
    catch (...) {
        return nullptr;
    }
}

void TaskMassProperties::setCurrentDatumObject(App::DocumentObject* object)
{
    clearCurrentDatumObject();
    auto* document = targetDocument();
    if (!document || !isTimelineSelectionActive(object)
        || object->getDocument() != document
        || !object->getNameInDocument()
        || !document->containsObject(object)
        || document->getObject(object->getNameInDocument()) != object
        || document->getObjectByID(object->getID()) != object) {
        return;
    }

    currentDatumDocumentName = object->getDocument()->getName();
    currentDatumDocumentUid =
        object->getDocument()->Uid.getValueStr();
    currentDatumDocumentAddress = object->getDocument();
    currentDatumName = object->getNameInDocument();
    currentDatumId = object->getID();
}

void TaskMassProperties::clearCurrentDatumObject()
{
    clearCurrentDatumOccurrence();
    currentDatumDocumentName.clear();
    currentDatumDocumentUid.clear();
    currentDatumDocumentAddress = nullptr;
    currentDatumName.clear();
    currentDatumId = -1;
}

App::DocumentObject*
TaskMassProperties::currentDatumOccurrenceRoot() const
{
    auto* document = targetDocument();
    if (!document || currentDatumOccurrenceRootName.empty()
        || currentDatumOccurrenceRootId < 0) {
        return nullptr;
    }

    try {
        auto* root =
            document->getObjectByID(currentDatumOccurrenceRootId);
        if (!root || !root->getNameInDocument()
            || currentDatumOccurrenceRootName
                != root->getNameInDocument()
            || !document->containsObject(root)
            || document->getObject(
                   currentDatumOccurrenceRootName.c_str()
               )
                != root
            || !isTimelineSelectionActive(root)) {
            return nullptr;
        }
        return root;
    }
    catch (...) {
        return nullptr;
    }
}

void TaskMassProperties::setCurrentDatumOccurrence(
    App::DocumentObject* root,
    const std::string& subName
)
{
    clearCurrentDatumOccurrence();
    auto* document = targetDocument();
    auto* datum = currentDatumObject();
    if (!document || !datum || !root
        || root->getDocument() != document
        || !root->getNameInDocument()
        || !document->containsObject(root)
        || document->getObject(root->getNameInDocument()) != root
        || document->getObjectByID(root->getID()) != root
        || !isTimelineSelectionActive(root)) {
        return;
    }

    std::string normalizedPath;
    if (!Measure::Internal::normalizeObjectPath(
            root,
            subName.c_str(),
            normalizedPath
        )) {
        return;
    }

    auto members =
        root->getSubObjectList(normalizedPath.c_str(), nullptr, false);
    if (members.empty()
        || !Measure::Internal::endpointRepresentsSource(
            members.back(),
            datum
        )) {
        return;
    }

    currentDatumOccurrenceRootName = root->getNameInDocument();
    currentDatumOccurrenceRootId = root->getID();
    currentDatumOccurrenceSubName = std::move(normalizedPath);
}

void TaskMassProperties::clearCurrentDatumOccurrence()
{
    currentDatumOccurrenceRootName.clear();
    currentDatumOccurrenceRootId = -1;
    currentDatumOccurrenceSubName.clear();
}

App::DocumentObject* TaskMassProperties::previewObject() const
{
    auto* document = targetDocument();
    if (!document || previewObjectName.empty() || previewObjectId < 0) {
        return nullptr;
    }

    auto* object = document->getObjectByID(previewObjectId);
    if (!object || previewObjectName != object->getNameInDocument()
        || !document->containsObject(object)
        || document->getObject(previewObjectName.c_str()) != object) {
        return nullptr;
    }
    return object;
}

void TaskMassProperties::clearPreviewObjectIdentity()
{
    previewObjectName.clear();
    previewObjectId = -1;
}

bool TaskMassProperties::startPreviewTransaction()
{
    if (previewTransaction) {
        return previewTransaction->ownsCurrentTransaction();
    }
    auto* document = targetDocument();
    if (!document) {
        return false;
    }
    clearPreviewObjectIdentity();
    try {
        previewTransaction =
            std::make_unique<OwnedMassPropertiesTransaction>(
                *document,
                "Preview Mass Properties"
            );
        return true;
    }
    catch (const Base::Exception& error) {
        Base::Console().error(
            "Could not start mass-properties preview: %s\n",
            error.what()
        );
    }
    catch (const std::exception& error) {
        Base::Console().error(
            "Could not start mass-properties preview: %s\n",
            error.what()
        );
    }
    return false;
}

bool TaskMassProperties::abortPreviewTransaction()
{
    ++previewGeneration;
    if (!previewTransaction) {
        clearPreviewObjectIdentity();
        return true;
    }
    if (!previewTransaction->abort()) {
        return false;
    }
    previewTransaction.reset();
    clearPreviewObjectIdentity();
    return true;
}

bool TaskMassProperties::finishDurableResult(
    std::unique_ptr<OwnedMassPropertiesTransaction> transaction
)
{
    if (!transaction || !transaction->commit()) {
        return false;
    }
    transaction.reset();

    try {
        // The durable object was created at document root by the exact
        // transaction above. This advances the task checkpoint without
        // re-parenting it into an active modeling Body.
        markCommandInteractionStateDurable();
    }
    catch (const Base::Exception& error) {
        Base::Console().error(
            "Mass-properties result was saved, but task finalization "
            "failed: %s\n",
            error.what()
        );
        return false;
    }

    if (!startPreviewTransaction()) {
        Base::Console().error(
            "Mass-properties result was saved, but a new preview "
            "transaction could not be opened.\n"
        );
        return false;
    }
    update(Gui::SelectionChanges());
    return true;
}

bool TaskMassProperties::eventFilter(QObject* watched, QEvent* event)
{
    Q_UNUSED(watched);

    if (event->type() == QEvent::ShortcutOverride || event->type() == QEvent::KeyPress) {
        auto* keyEvent = static_cast<QKeyEvent*>(event);
        if (keyEvent->key() == Qt::Key_Delete) {
            if (!targetDocument()) {
                return false;
            }
            if (event->type() == QEvent::ShortcutOverride) {
                event->accept();
                return true;
            }

            if (panel->ui.objectList->hasFocus()) {
                QList<QListWidgetItem*> selectedItems = panel->ui.objectList->selectedItems();
                if (selectedItems.empty()) {
                    event->accept();
                    return true;
                }

                std::vector<QString> toRemove;
                for (auto* item : selectedItems) {
                    toRemove.push_back(item->data(Qt::UserRole).toString());
                }

                if (toRemove.size() == static_cast<std::size_t>(panel->ui.objectList->count())) {
                    Gui::Selection().clearSelection(
                        targetDocumentName.c_str()
                    );
                }

                for (const auto& userData : toRemove) {
                    QStringList parts = userData.split(QStringLiteral("|"));
                    if (parts.size() == 3) {
                        std::string docName = parts[0].toStdString();
                        std::string objName = parts[1].toStdString();
                        std::string subName = parts[2].toStdString();
                        Gui::Selection().rmvSelection(
                            docName.empty() ? nullptr : docName.c_str(),
                            objName.empty() ? nullptr : objName.c_str(),
                            subName.empty() ? nullptr : subName.c_str()
                        );
                    }
                }

                event->accept();
                return true;
            }

            event->accept();
            return true;
        }
    }

    return Gui::TaskView::TaskDialog::eventFilter(watched, event);
}

void TaskMassProperties::modifyStandardButtons(QDialogButtonBox* box)
{
    QPushButton* closeButton = box->button(QDialogButtonBox::Abort);
    closeButton->setText(tr("Close"));

    QPushButton* saveButton = box->button(QDialogButtonBox::Apply);
    saveButton->setText(tr("Save"));
    QObject::connect(saveButton, &QPushButton::released, this, &TaskMassProperties::saveResult);

    QPushButton* resetButton = box->button(QDialogButtonBox::Reset);
    resetButton->setText(tr("Reset"));
    QObject::connect(resetButton, &QPushButton::released, [this]() {
        if (!targetDocument()) {
            return;
        }
        Gui::Selection().clearSelection(targetDocumentName.c_str());
        removeTemporaryObjects();
        clearUiFields();
        panel->ui.objectList->clear();
    });
}

void TaskMassProperties::invoke()
{}

bool TaskMassProperties::accept()
{
    return false;
}

bool TaskMassProperties::reject()
{
    return abortPreviewTransaction();
}

void TaskMassProperties::escape()
{
    if (!targetDocument()) {
        return;
    }
    if (Gui::Selection()
            .getSelection(
                targetDocumentName.c_str(),
                Gui::ResolveMode::NoResolve
            )
            .empty()) {
        Gui::Control().reject(targetDocument());
        return;
    }

    Gui::Selection().clearSelection(targetDocumentName.c_str());
    this->removeTemporaryObjects();
    this->clearUiFields();
    panel->ui.objectList->clear();

    selectingCustomCoordSystem = false;
    clearCurrentDatumObject();
    hasCurrentDatumPlacement = false;
    panel->ui.customEdit->clear();

    currentInfo = MassPropertiesData {};
}

void TaskMassProperties::removeTemporaryObjects()
{
    ++previewGeneration;
    App::Document* document = targetDocument();
    App::DocumentObject* object = previewObject();
    if (document && object) {
        document->removeObject(object->getNameInDocument());
    }
    clearPreviewObjectIdentity();
}


void TaskMassProperties::clearUiFields()
{
    currentInfo = MassPropertiesData {};
    objectsToMeasure.clear();
    objectOccurrences.clear();

    panel->ui.volumeEdit->clear();
    panel->ui.massEdit->clear();
    panel->ui.densityEdit->clear();
    panel->ui.surfaceAreaEdit->clear();

    panel->ui.cogXText->clear();
    panel->ui.cogYText->clear();
    panel->ui.cogZText->clear();

    panel->ui.covXText->clear();
    panel->ui.covYText->clear();
    panel->ui.covZText->clear();

    panel->ui.inertiaJoxText->clear();
    panel->ui.inertiaJxyText->clear();
    panel->ui.inertiaJzxText->clear();
    panel->ui.inertiaJoyText->clear();
    panel->ui.inertiaJzyText->clear();
    panel->ui.inertiaJozText->clear();

    panel->ui.inertiaJxText->clear();
    panel->ui.inertiaJyText->clear();
    panel->ui.inertiaJzText->clear();

    panel->ui.axisInertiaText->clear();
}

void TaskMassProperties::onSelectionChanged(const Gui::SelectionChanges& msg)
{
    if (isUpdating || !targetDocument() || !previewTransaction
        || !previewTransaction->ownsCurrentTransaction()) {
        return;
    }

    if (msg.Type != Gui::SelectionChanges::AddSelection
        && msg.Type != Gui::SelectionChanges::RmvSelection
        && msg.Type != Gui::SelectionChanges::SetSelection
        && msg.Type != Gui::SelectionChanges::ClrSelection) {

        return;
    }
    if (msg.pDocName && *msg.pDocName
        && targetDocumentName != msg.pDocName) {
        return;
    }

    if (!selectingCustomCoordSystem && msg.Type == Gui::SelectionChanges::AddSelection
        && msg.pDocName && msg.pObjectName && msg.pSubName && msg.pSubName[0]) {
        auto* doc = App::GetApplication().getDocument(msg.pDocName);
        if (!doc) {
            update(msg);
            return;
        }

        auto* obj = doc->getObject(msg.pObjectName);
        if (!obj) {
            update(msg);
            return;
        }

        App::SubObjectT sub(obj, msg.pSubName);
        if (sub.hasSubElement()) {
            std::string promotedSubName = sub.getSubNameNoElement();
            if (promotedSubName != msg.pSubName) {
                {
                    QScopedValueRollback<bool> updatingGuard(isUpdating, true);
                    Gui::Selection().rmvSelection(msg.pDocName, msg.pObjectName, msg.pSubName);
                    if (promotedSubName.empty()) {
                        Gui::Selection().addSelection(msg.pDocName, msg.pObjectName);
                    }
                    else {
                        Gui::Selection().addSelection(
                            msg.pDocName,
                            msg.pObjectName,
                            promotedSubName.c_str(),
                            msg.x,
                            msg.y,
                            msg.z
                        );
                    }
                }
                update(msg);
                return;
            }
        }
    }

    update(msg);
}

void TaskMassProperties::update(const Gui::SelectionChanges& msg)
{
    (void)msg;
    try {
        tryUpdate();
    }
    catch (const Base::Exception& e) {
        Base::Console().error("Mass Properties update failed: %s\n", e.what());
    }
    catch (const std::exception& e) {
        Base::Console().error("Mass Properties update failed: %s\n", e.what());
    }
}


void TaskMassProperties::tryUpdate()
{
    if (isUpdating) {
        return;
    }

    QScopedValueRollback<bool> updatingGuard(isUpdating, true);
    if (!targetDocument() || !previewTransaction
        || !previewTransaction->ownsCurrentTransaction()) {
        return;
    }

    auto guiSelection = Gui::Selection().getSelection(
        targetDocumentName.c_str(),
        Gui::ResolveMode::NoResolve
    );

    if (guiSelection.empty()) {
        clearUiFields();
        objectsToMeasure.clear();
        panel->ui.objectList->clear();
        removeTemporaryObjects();
        return;
    }

    if (!selectingCustomCoordSystem) {
        bool promotedSelection = false;
        for (const auto& sel : guiSelection) {
            if (!sel.pObject || !sel.pObject->getDocument() || !sel.SubName || !sel.SubName[0]) {
                continue;
            }

            App::SubObjectT sub(sel.pObject, sel.SubName);
            if (!sub.hasSubElement()) {
                continue;
            }

            std::string promotedSubName = sub.getSubNameNoElement();
            if (promotedSubName == sel.SubName) {
                continue;
            }

            Gui::Selection().rmvSelection(
                sel.pObject->getDocument()->getName(),
                sel.pObject->getNameInDocument(),
                sel.SubName
            );
            if (promotedSubName.empty()) {
                Gui::Selection().addSelection(
                    sel.pObject->getDocument()->getName(),
                    sel.pObject->getNameInDocument()
                );
            }
            else {
                Gui::Selection().addSelection(
                    sel.pObject->getDocument()->getName(),
                    sel.pObject->getNameInDocument(),
                    promotedSubName.c_str()
                );
            }
            promotedSelection = true;
        }

        if (promotedSelection) {
            isUpdating = false;
            tryUpdate();
            return;
        }
    }

    if (!selectingCustomCoordSystem) {
        bool hasInvisibleSelection = false;
        std::vector<std::tuple<App::DocumentObject*, std::string, bool>> selectedObjects;
        selectedObjects.reserve(guiSelection.size());

        for (const auto& sel : guiSelection) {
            if (!sel.pObject) {
                continue;
            }

            App::DocumentObject* pickedObject = sel.pObject;
            if (sel.pResolvedObject && sel.pResolvedObject != sel.pObject) {
                pickedObject = sel.pResolvedObject;
            }
            if (sel.SubName && sel.SubName[0]) {
                App::SubObjectT sub(sel.pObject, sel.SubName);
                if (auto* leaf = sub.getSubObject()) {
                    pickedObject = leaf;
                }
            }

            bool isVisible = isTimelineSelectionActive(sel.pObject)
                && isTimelineSelectionActive(pickedObject)
                && (isPresentedForMassProperties(sel.pObject)
                    || isPresentedForMassProperties(pickedObject));

            if (!isVisible) {
                hasInvisibleSelection = true;
            }

            if (sel.SubName && sel.SubName[0]) {
                selectedObjects.emplace_back(sel.pObject, sel.SubName, isVisible);
            }
            else {
                selectedObjects.emplace_back(sel.pObject, std::string(), isVisible);
            }
        }

        if (hasInvisibleSelection) {
            std::unordered_set<std::string> seen;
            isUpdating = true;
            Gui::Selection().clearSelection(targetDocumentName.c_str());

            for (const auto& selected : selectedObjects) {
                bool isVisible = std::get<2>(selected);
                if (!isVisible) {
                    continue;
                }

                App::DocumentObject* obj = std::get<0>(selected);
                if (!obj || !obj->getDocument()) {
                    continue;
                }

                const std::string& subName = std::get<1>(selected);
                std::string key = obj->getDocument()->getName();
                key += '|';
                key += obj->getNameInDocument();
                key += '|';
                key += subName;

                if (!seen.insert(key).second) {
                    continue;
                }

                if (subName.empty()) {
                    Gui::Selection().addSelection(
                        obj->getDocument()->getName(),
                        obj->getNameInDocument()
                    );
                }
                else {
                    Gui::Selection().addSelection(
                        obj->getDocument()->getName(),
                        obj->getNameInDocument(),
                        subName.c_str()
                    );
                }
            }

            isUpdating = false;
            tryUpdate();

            return;
        }
    }

    objectsToMeasure.clear();
    objectOccurrences.clear();
    App::DocumentObject const* referenceDatum = nullptr;

    panel->ui.objectList->clear();

    auto coordLabel = [](App::DocumentObject* obj) {
        if (auto* datum = freecad_cast<App::DatumElement*>(obj)) {
            if (auto* lcs = datum->getLCS()) {
                return lcs->getFullLabel();
            }
        }
        if (auto* lcs = freecad_cast<App::LocalCoordinateSystem*>(obj)) {
            return lcs->getFullLabel();
        }
        if (auto* origin = freecad_cast<App::Origin*>(obj)) {
            return origin->getFullLabel();
        }

        return obj->getFullLabel();
    };


    auto isReferenceObject = [](App::DocumentObject* obj) {
        if (!obj) {
            return false;
        }
        auto datum = freecad_cast<App::DatumElement*>(obj);
        if (datum && datum->getLCS()) {
            return true;
        }
        if (freecad_cast<App::LocalCoordinateSystem*>(obj)) {
            return true;
        }
        if (freecad_cast<App::Origin*>(obj)) {
            return true;
        }
        if (obj->getTypeId().getName() == std::string("PartDesign::CoordinateSystem")) {
            return true;
        }

        return obj->isDerivedFrom<App::Line>();
    };

    auto getPlacementFromObject = [](App::DocumentObject* obj) {
        if (!obj) {
            return Base::Placement();
        }
        if (auto* prop = freecad_cast<App::PropertyPlacement*>(obj->getPropertyByName("Placement"))) {
            return prop->getValue();
        }
        return Base::Placement();
    };

    auto getGlobalPlacement = [&](App::DocumentObject* root,
                                  const char* subName,
                                  App::DocumentObject* resolvedObject = nullptr) {
        std::string normalizedPath;
        if (!Measure::Internal::normalizeObjectPath(
                root,
                subName,
                normalizedPath
            )) {
            throw Base::ValueError(
                "The selected occurrence path is no longer valid"
            );
        }

        const auto members =
            root->getSubObjectList(normalizedPath.c_str(), nullptr, false);
        if (members.empty()) {
            throw Base::ValueError(
                "The selected occurrence has no resolvable object"
            );
        }

        auto* endpoint = members.back();
        auto* target = resolvedObject
                && Measure::Internal::endpointRepresentsSource(
                    endpoint,
                    resolvedObject
                )
            ? resolvedObject
            : endpoint;
        return App::GeoFeature::getGlobalPlacement(
            target,
            root,
            normalizedPath
        );
    };

    auto occurrenceIdentity = [](
                                  App::DocumentObject* root,
                                  const std::string& subName,
                                  App::DocumentObject* endpoint
                              ) {
        std::vector<int> pathEnds;
        const auto members =
            root->getSubObjectList(subName.c_str(), &pathEnds, false);
        std::ostringstream key;

        std::size_t firstOccurrence = members.size();
        for (std::size_t index = 0; index < members.size(); ++index) {
            if (members[index]
                && members[index]->hasExtension(
                    App::LinkBaseExtension::getExtensionClassTypeId()
                )) {
                firstOccurrence = index;
                break;
            }
        }

        if (firstOccurrence == members.size()) {
            key << "object|"
                << endpoint->getDocument()->getName() << '|'
                << endpoint->getID();
            return key.str();
        }

        auto* occurrence = members[firstOccurrence];
        const auto suffixOffset =
            firstOccurrence < pathEnds.size()
            ? static_cast<std::size_t>(pathEnds[firstOccurrence])
            : std::size_t {0};
        key << "occurrence|"
            << occurrence->getDocument()->getName() << '|'
            << occurrence->getID() << '|'
            << subName.substr(
                   std::min(suffixOffset, subName.size())
               )
            << '|'
            << endpoint->getDocument()->getName() << '|'
            << endpoint->getID();
        return key.str();
    };

    std::unordered_set<std::string> objectKeys;
    auto addObject = [&](
                         App::DocumentObject* occurrenceRoot,
                         const std::string& occurrenceSubName,
                         App::DocumentObject* sourceObject
                     ) {
        if (!isTimelineSelectionActive(occurrenceRoot)
            || !isTimelineSelectionActive(sourceObject)) {
            return false;
        }

        std::string normalizedPath;
        if (!Measure::Internal::normalizeObjectPath(
                occurrenceRoot,
                occurrenceSubName.c_str(),
                normalizedPath
            )) {
            return false;
        }

        Measure::Internal::ResolvedOccurrence occurrence;
        if (!Measure::Internal::resolveShapeOccurrence(
                occurrenceRoot,
                normalizedPath,
                occurrence
            )
            || !Measure::Internal::endpointRepresentsSource(
                occurrence.endpoint,
                sourceObject
            )
            || !isTimelineSelectionActive(
                occurrence.materialOwner
            )) {
            return false;
        }

        const TopAbs_ShapeEnum shapeType =
            occurrence.shape.ShapeType();
        if (shapeType != TopAbs_SOLID
            && shapeType != TopAbs_COMPSOLID
            && shapeType != TopAbs_SHELL
            && shapeType != TopAbs_FACE
            && shapeType != TopAbs_COMPOUND) {
            return false;
        }

        const std::string objectKey = occurrenceIdentity(
            occurrenceRoot,
            normalizedPath,
            occurrence.endpoint
        );
        if (!objectKeys.insert(objectKey).second) {
            return true;
        }

        const Base::Placement sourceParentPlacement =
            occurrence.placement
            * getPlacementFromObject(sourceObject).inverse();
        objectsToMeasure.push_back({
            occurrence.materialOwner,
            occurrence.shape,
            Base::Placement(),
            sourceObject,
            "",
            sourceParentPlacement,
        });
        objectOccurrences.push_back({
            occurrenceRoot,
            normalizedPath,
        });
        return true;
    };

    std::unordered_set<App::DocumentObject*> activeDefinitions;
    auto collectBodies =
        [&](auto&& self,
            App::DocumentObject* occurrenceRoot,
            const std::string& occurrenceSubName,
            App::DocumentObject* object) -> void {
        if (!isTimelineSelectionActive(object)) {
            return;
        }

        auto* resolved = object->getLinkedObject(true);
        if (!resolved) {
            resolved = object;
        }
        if (!isTimelineSelectionActive(resolved)
            || !activeDefinitions.insert(resolved).second) {
            return;
        }

        bool handled = false;
        if (resolved->getTypeId().getName()
            == std::string("PartDesign::Body")) {
            auto* tipProperty = freecad_cast<App::PropertyLink*>(
                resolved->getPropertyByName("Tip")
            );
            auto* tip = tipProperty ? tipProperty->getValue() : nullptr;
            if (tip) {
                if (object->hasExtension(
                        App::LinkBaseExtension::getExtensionClassTypeId()
                    )) {
                    addObject(
                        occurrenceRoot,
                        occurrenceSubName,
                        object
                    );
                }
                else {
                    std::string tipPath = occurrenceSubName;
                    tipPath += tip->getNameInDocument();
                    tipPath += '.';
                    addObject(occurrenceRoot, tipPath, tip);
                }
            }
            handled = true;
        }
        else if (resolved->getExtensionByType<App::GroupExtension>(
                     true
                 )) {
            for (const auto& relativePath : object->getSubObjects()) {
                std::string childPath = occurrenceSubName;
                childPath += relativePath;
                App::SubObjectT childOccurrence(
                    occurrenceRoot,
                    childPath.c_str()
                );
                auto* child = childOccurrence.getSubObject();
                if (child) {
                    self(
                        self,
                        occurrenceRoot,
                        childPath,
                        child
                    );
                }
            }
            handled = true;
        }

        if (!handled) {
            addObject(occurrenceRoot, occurrenceSubName, object);
        }
        activeDefinitions.erase(resolved);
    };

    hasCurrentDatumPlacement = false;

    if (selectingCustomCoordSystem) {

        for (const auto& selObj : guiSelection) {
            App::DocumentObject* coordSystem = selObj.pObject;

            if (selObj.pResolvedObject && selObj.pResolvedObject != selObj.pObject) {
                coordSystem = selObj.pResolvedObject;
            }

            if (selObj.SubName && selObj.SubName[0]) {
                App::SubObjectT sub(selObj.pObject, selObj.SubName);

                if (auto* leaf = sub.getSubObject()) {
                    coordSystem = leaf;
                }
            }

            if (isReferenceObject(coordSystem)) {
                if (!isTimelineSelectionActive(coordSystem)) {
                    continue;
                }
                panel->ui.customEdit->setText(QString::fromStdString(coordLabel(coordSystem)));
                setCurrentDatumObject(coordSystem);
                setCurrentDatumOccurrence(
                    selObj.pObject,
                    selObj.SubName ? selObj.SubName : ""
                );
                currentDatumPlacement
                    = getGlobalPlacement(selObj.pObject, selObj.SubName, selObj.pResolvedObject);
                hasCurrentDatumPlacement = true;
                selectingCustomCoordSystem = false;

                isUpdating = true;
                Gui::Selection().clearSelection(
                    targetDocumentName.c_str()
                );
                for (const auto& sel : savedSelection) {
                    if (std::get<2>(sel).empty()) {
                        Gui::Selection().addSelection(
                            std::get<0>(sel).c_str(),
                            std::get<1>(sel).c_str()
                        );
                    }
                    else {
                        Gui::Selection().addSelection(
                            std::get<0>(sel).c_str(),
                            std::get<1>(sel).c_str(),
                            std::get<2>(sel).c_str()
                        );
                    }
                }
                savedSelection.clear();
                isUpdating = false;
                tryUpdate();
                return;
            }
        }
    }

    for (const auto& selObj : guiSelection) {
        if (!selObj.pObject) {
            continue;
        }

        App::DocumentObject* displayObject = selObj.pObject;
        if (selObj.pResolvedObject && selObj.pResolvedObject != selObj.pObject) {
            displayObject = selObj.pResolvedObject;
        }

        if (selObj.SubName && selObj.SubName[0]) {
            App::SubObjectT sub(selObj.pObject, selObj.SubName);
            if (auto* leaf = sub.getSubObject()) {
                displayObject = leaf;
            }
        }

        if (!isTimelineSelectionActive(selObj.pObject)
            || !isTimelineSelectionActive(displayObject)) {
            continue;
        }

        bool shouldAddToList = false;
        if (!isReferenceObject(displayObject)) {
            if (displayObject->isDerivedFrom(Base::Type::fromName("Sketcher::SketchObject"))) {
                continue;
            }

            if (!isPresentedForMassProperties(selObj.pObject)
                && !isPresentedForMassProperties(displayObject)) {
                continue;
            }

            shouldAddToList = true;
        }

        App::DocumentObject* coordSystem = selObj.pObject;
        if (selObj.pResolvedObject && selObj.pResolvedObject != selObj.pObject) {
            coordSystem = selObj.pResolvedObject;
        }

        if (selObj.SubName && selObj.SubName[0]) {
            App::SubObjectT sub(selObj.pObject, selObj.SubName);
            if (auto* leaf = sub.getSubObject()) {
                coordSystem = leaf;
            }
        }

        if (isReferenceObject(coordSystem)) {
            if (!isTimelineSelectionActive(coordSystem)) {
                continue;
            }
            if (currentMode == MassPropertiesMode::Custom && !selectingCustomCoordSystem) {
                setCurrentDatumObject(coordSystem);
                setCurrentDatumOccurrence(
                    selObj.pObject,
                    selObj.SubName ? selObj.SubName : ""
                );
                currentDatumPlacement
                    = getGlobalPlacement(selObj.pObject, selObj.SubName, selObj.pResolvedObject);
                hasCurrentDatumPlacement = true;
                panel->ui.customEdit->setText(QString::fromStdString(coordLabel(coordSystem)));
                referenceDatum = currentDatumObject();
                break;
            }
            continue;
        }

        std::string occurrencePath;
        if (!Measure::Internal::normalizeObjectPath(
                selObj.pObject,
                selObj.SubName,
                occurrencePath
            )) {
            continue;
        }
        const auto occurrenceMembers =
            selObj.pObject->getSubObjectList(
                occurrencePath.c_str(),
                nullptr,
                false
            );
        if (occurrenceMembers.empty()) {
            continue;
        }
        auto* leaf = occurrenceMembers.back();

        activeDefinitions.clear();
        size_t objectsBefore = objectsToMeasure.size();
        collectBodies(
            collectBodies,
            selObj.pObject,
            occurrencePath,
            leaf
        );

        if (shouldAddToList && objectsToMeasure.size() > objectsBefore) {
            auto* item = new QListWidgetItem(QString::fromStdString(displayObject->getFullLabel()));
            QString docName;
            if (auto* doc = selObj.pObject->getDocument()) {
                docName = QString::fromUtf8(doc->getName());
            }
            QString objName = QString::fromUtf8(selObj.pObject->getNameInDocument());
            QString subName = selObj.SubName ? QString::fromUtf8(selObj.SubName) : QString();
            item->setData(
                Qt::UserRole,
                docName + QStringLiteral("|") + objName + QStringLiteral("|") + subName
            );
            panel->ui.objectList->addItem(item);
        }
    }

    if (currentMode == MassPropertiesMode::Custom) {
        referenceDatum = currentDatumObject();
    }
    else {
        panel->ui.customEdit->clear();
    }

    if (currentMode == MassPropertiesMode::Custom && !referenceDatum) {
        this->clearUiFields();
        this->removeTemporaryObjects();
        return;
    }

    if (panel->ui.objectList->count() == 0) {
        this->clearUiFields();
        this->removeTemporaryObjects();
        return;
    }

    updateInertiaVisibility();

    MassPropertiesData info = CalculateMassProperties(
        objectsToMeasure,
        currentMode,
        referenceDatum,
        hasCurrentDatumPlacement ? &currentDatumPlacement : nullptr
    );

    if (info.volume.getValue() == 0.0 && info.mass.getValue() == 0.0) {
        this->clearUiFields();
        this->removeTemporaryObjects();
        objectsToMeasure.clear();
        panel->ui.objectList->clear();
        return;
    }

    currentInfo = info;

    if (currentMode == MassPropertiesMode::Custom && referenceDatum) {
        auto applyOriginOffset = [&](const Base::Vector3d& originPos) {
            info.cog -= originPos;
            info.cov -= originPos;
        };


        if (!referenceDatum->isDerivedFrom<App::Line>()) {
            if (hasCurrentDatumPlacement) {
                applyOriginOffset(currentDatumPlacement.getPosition());
            }
            else if (auto datum = freecad_cast<const App::DatumElement*>(referenceDatum)) {
                if (datum->getLCS()) {
                    applyOriginOffset(datum->getBasePoint());
                }
            }
            else if (auto lcs = freecad_cast<const App::LocalCoordinateSystem*>(referenceDatum)) {
                applyOriginOffset(lcs->Placement.getValue().getPosition());
            }
            else if (auto origin = freecad_cast<const App::Origin*>(referenceDatum)) {
                applyOriginOffset(origin->Placement.getValue().getPosition());
            }
        }
    }

    const int decimals = Base::UnitsApi::getDecimals();
    const int denominator = Base::UnitsApi::getDenominator();

    auto setText =
        [&](QLineEdit* edit, const Base::Quantity& quantity, const QString& suffix = QString()) {
            Base::Quantity q(quantity);
            if (std::fabs(q.getValue()) < Base::Precision::Confusion()) {
                q.setValue(0.0);
            }
            Base::QuantityFormat format(Base::QuantityFormat::Fixed, decimals);
            format.setDenominator(denominator);
            q.setFormat(format);

            std::string text;
            auto schema = Base::UnitsApi::createSchema(static_cast<std::size_t>(unitsSchemaIndex));
            if (schema) {
                text = schema->translate(q);
            }
            else {
                text = Base::UnitsApi::schemaTranslate(q);
            }
            edit->setText(QString::fromUtf8(text.c_str()) + suffix);
            edit->setCursorPosition(0);
        };

    const QString densitySuffix = objectsToMeasure.size() > 1 ? tr(" (Average)") : QString();

    setText(panel->ui.volumeEdit, info.volume);
    setText(panel->ui.massEdit, info.mass);
    setText(panel->ui.surfaceAreaEdit, info.surfaceArea);
    setText(panel->ui.densityEdit, info.density, densitySuffix);

    setText(panel->ui.cogXText, Base::Quantity(info.cog.x, Base::Unit::Length));
    setText(panel->ui.cogYText, Base::Quantity(info.cog.y, Base::Unit::Length));
    setText(panel->ui.cogZText, Base::Quantity(info.cog.z, Base::Unit::Length));
    setText(panel->ui.covXText, Base::Quantity(info.cov.x, Base::Unit::Length));
    setText(panel->ui.covYText, Base::Quantity(info.cov.y, Base::Unit::Length));
    setText(panel->ui.covZText, Base::Quantity(info.cov.z, Base::Unit::Length));

    setText(panel->ui.inertiaJoxText, Base::Quantity(info.inertiaJo.x, Base::Unit::Inertia));
    setText(panel->ui.inertiaJoyText, Base::Quantity(info.inertiaJo.y, Base::Unit::Inertia));
    setText(panel->ui.inertiaJozText, Base::Quantity(info.inertiaJo.z, Base::Unit::Inertia));
    setText(panel->ui.inertiaJxyText, Base::Quantity(info.inertiaJCross.x, Base::Unit::Inertia));
    setText(panel->ui.inertiaJzxText, Base::Quantity(info.inertiaJCross.y, Base::Unit::Inertia));
    setText(panel->ui.inertiaJzyText, Base::Quantity(info.inertiaJCross.z, Base::Unit::Inertia));

    setText(panel->ui.inertiaJxText, Base::Quantity(info.inertiaJ.x, Base::Unit::Inertia));
    setText(panel->ui.inertiaJyText, Base::Quantity(info.inertiaJ.y, Base::Unit::Inertia));
    setText(panel->ui.inertiaJzText, Base::Quantity(info.inertiaJ.z, Base::Unit::Inertia));
    setText(panel->ui.axisInertiaText, Base::Quantity(info.axisInertia, Base::Unit::Inertia));

    const bool hasAxisSelection = currentMode == MassPropertiesMode::Custom && referenceDatum
        && referenceDatum->isDerivedFrom<App::Line>();

    const auto infoSnapshot = currentInfo;
    const auto generation = ++previewGeneration;
    QTimer::singleShot(0, this, [this, infoSnapshot, hasAxisSelection, generation]() {
        App::Document* doc = targetDocument();
        if (generation != previewGeneration || !doc || !previewTransaction
            || !previewTransaction->ownsCurrentTransaction()) {
            return;
        }

        App::DocumentObject* obj = previewObject();
        if (!obj) {
            obj = doc->addObject("Measure::Result", "MassPropertiesPreview");
            if (!obj) {
                return;
            }
            previewObjectName = obj->getNameInDocument();
            previewObjectId = obj->getID();
        }

        obj->Visibility.setValue(true);

        auto* guiDoc = Gui::Application::Instance
            ? Gui::Application::Instance->getDocument(doc)
            : nullptr;
        if (!guiDoc) {
            return;
        }

        auto* view = dynamic_cast<Gui::ViewProviderDocumentObject*>(guiDoc->getViewProvider(obj));
        if (!view) {
            return;
        }

        if (auto* resultView = dynamic_cast<ViewProviderMassPropertiesResult*>(view)) {
            resultView->setCenters(infoSnapshot.cog, infoSnapshot.cov);
            resultView->setPrincipalAxes(
                infoSnapshot.cog,
                infoSnapshot.principalAxis1,
                infoSnapshot.principalAxis2,
                infoSnapshot.principalAxis3,
                !hasAxisSelection
            );
        }

        view->setShowable(true);
        view->ShowInTree.setValue(false);
        view->show();
    });
}

void TaskMassProperties::updateInertiaVisibility()
{
    auto* datum = currentDatumObject();
    const bool hasAxisSelection = currentMode == MassPropertiesMode::Custom && datum
        && datum->isDerivedFrom<App::Line>();

    panel->ui.inertiaMatrixWidget->setVisible(!hasAxisSelection);
    panel->ui.inertiaDiagWidget->setVisible(!hasAxisSelection);
    panel->ui.inertiaLcsWidget->setVisible(!hasAxisSelection);
    panel->ui.axisInertiaWidget->setVisible(hasAxisSelection);
    panel->ui.inertiaSeparator->setVisible(!hasAxisSelection);
    panel->ui.inertiaDiagSpacer1->changeSize(hasAxisSelection ? 0 : 8, 20);
    panel->ui.inertiaDiagSpacer2->changeSize(hasAxisSelection ? 0 : 8, 20);
    panel->ui.inertiaDiagLayout->invalidate();
    panel->ui.inertiaMatrixLabel->setVisible(!hasAxisSelection);
    panel->ui.inertiaPrincipalLabel->setVisible(!hasAxisSelection);
}

void TaskMassProperties::createDatum(
    const Base::Vector3d& position,
    const std::string& name,
    bool removeExisting
)
{
    if (isUpdating && removeExisting) {
        return;
    }

    std::unique_ptr<OwnedMassPropertiesTransaction> transaction;
    try {
        tryUpdate();
        if (objectsToMeasure.empty()
            || panel->ui.objectList->count() == 0
            || (currentInfo.volume.getValue() == 0.0
                && currentInfo.mass.getValue() == 0.0)
            || !previewTransaction
            || !previewTransaction->ownsCurrentTransaction()) {
            return;
        }
        if (!abortPreviewTransaction()) {
            return;
        }
        App::Document* doc = targetDocument();
        if (!doc) {
            return;
        }
        transaction = std::make_unique<OwnedMassPropertiesTransaction>(
            *doc,
            "Create Datum Point"
        );

        App::DocumentObject* datum = doc->getObject(name.c_str());

        if (removeExisting && datum) {
            doc->removeObject(name.c_str());
        }

        datum = doc->addObject("Part::DatumPoint", name.c_str());
        if (!datum) {
            throw Base::RuntimeError(
                "The datum-point object could not be created"
            );
        }

        App::Property* baseProp = datum->getPropertyByName("Placement");
        App::PropertyPlacement* prop = freecad_cast<App::PropertyPlacement*>(baseProp);
        if (!prop) {
            throw Base::RuntimeError(
                "The datum-point object has no Placement property"
            );
        }
        Base::Placement plm;
        plm.setPosition(position);
        prop->setValue(plm);

        doc->recompute();
        if (!finishDurableResult(std::move(transaction))) {
            startPreviewTransaction();
            update(Gui::SelectionChanges());
        }
    }
    catch (const Base::Exception& e) {
        Base::Console().error("Datum Creation failed: %s\n", e.what());
        transaction.reset();
        startPreviewTransaction();
        update(Gui::SelectionChanges());
    }
    catch (const std::exception& e) {
        Base::Console().error("Datum Creation failed: %s", e.what());
        transaction.reset();
        startPreviewTransaction();
        update(Gui::SelectionChanges());
    }
}

void TaskMassProperties::createLCS(std::string name, bool removeExisting)
{
    if (isUpdating && removeExisting) {
        return;
    }

    std::unique_ptr<OwnedMassPropertiesTransaction> transaction;
    try {
        tryUpdate();
        if (objectsToMeasure.empty()
            || panel->ui.objectList->count() == 0
            || (currentInfo.volume.getValue() == 0.0
                && currentInfo.mass.getValue() == 0.0)
            || !previewTransaction
            || !previewTransaction->ownsCurrentTransaction()) {
            return;
        }
        if (!abortPreviewTransaction()) {
            return;
        }
        App::Document* doc = targetDocument();
        if (!doc) {
            return;
        }
        transaction = std::make_unique<OwnedMassPropertiesTransaction>(
            *doc,
            "Create LCS"
        );

        App::DocumentObject* LCS = doc->getObject(name.c_str());

        if (removeExisting && LCS) {
            doc->removeObject(name.c_str());
        }
        LCS = doc->addObject("Part::LocalCoordinateSystem", name.c_str());
        if (!LCS) {
            throw Base::RuntimeError(
                "The local coordinate system could not be created"
            );
        }

        App::Property* baseProp = LCS->getPropertyByName("Placement");
        App::PropertyPlacement* prop = freecad_cast<App::PropertyPlacement*>(baseProp);
        if (!prop) {
            throw Base::RuntimeError(
                "The local coordinate system has no Placement property"
            );
        }
        Base::Placement plm;
        plm.setPosition(currentInfo.cog);

        Base::Matrix4D mat;
        mat.setToUnity();

        mat[0][0] = currentInfo.principalAxis1.x;
        mat[1][0] = currentInfo.principalAxis1.y;
        mat[2][0] = currentInfo.principalAxis1.z;

        mat[0][1] = currentInfo.principalAxis2.x;
        mat[1][1] = currentInfo.principalAxis2.y;
        mat[2][1] = currentInfo.principalAxis2.z;

        mat[0][2] = currentInfo.principalAxis3.x;
        mat[1][2] = currentInfo.principalAxis3.y;
        mat[2][2] = currentInfo.principalAxis3.z;

        plm.setRotation(mat);

        prop->setValue(plm);

        LCS->Visibility.setValue(true);
        if (auto* lcsObj = freecad_cast<App::LocalCoordinateSystem*>(LCS)) {
            for (auto* plane : lcsObj->planes()) {
                if (plane) {
                    plane->Visibility.setValue(false);
                }
            }
        }

        doc->recompute();
        if (!finishDurableResult(std::move(transaction))) {
            startPreviewTransaction();
            update(Gui::SelectionChanges());
        }
    }
    catch (const Base::Exception& e) {
        Base::Console().error("LCS Creation failed: %s\n", e.what());
        transaction.reset();
        startPreviewTransaction();
        update(Gui::SelectionChanges());
    }
    catch (const std::exception& e) {
        Base::Console().error("LCS Creation failed: %s", e.what());
        transaction.reset();
        startPreviewTransaction();
        update(Gui::SelectionChanges());
    }
}

void TaskMassProperties::onCogDatumButtonPressed()
{
    createDatum(currentInfo.cog, "Center_of_Gravity", false);
}

void TaskMassProperties::onCovDatumButtonPressed()
{
    createDatum(currentInfo.cov, "Center_of_Volume", false);
}

void TaskMassProperties::onLcsButtonPressed()
{
    auto* datum = currentDatumObject();
    const bool hasAxisSelection = currentMode == MassPropertiesMode::Custom && datum
        && datum->isDerivedFrom<App::Line>();
    if (!hasAxisSelection) {
        createLCS("Principal_Axes_LCS", false);
    }
}

void TaskMassProperties::onSelectCustomCoordinateSystem()
{
    if (!targetDocument() || !previewTransaction
        || !previewTransaction->ownsCurrentTransaction()) {
        return;
    }
    selectingCustomCoordSystem = true;
    savedSelection.clear();

    auto guiSelection = Gui::Selection().getSelection(
        targetDocumentName.c_str(),
        Gui::ResolveMode::NoResolve
    );
    for (const auto& sel : guiSelection) {
        if (!sel.pObject || !sel.pObject->getDocument()) {
            continue;
        }
        std::string docName = sel.pObject->getDocument()->getName();
        std::string objName = sel.pObject->getNameInDocument();
        std::string subName = (sel.SubName && sel.SubName[0]) ? sel.SubName : "";
        savedSelection.emplace_back(docName, objName, subName);
    }
}

void TaskMassProperties::onCoordinateSystemChanged(MassPropertiesMode coordSystemMode)
{
    if (!targetDocument() || !previewTransaction
        || !previewTransaction->ownsCurrentTransaction()) {
        return;
    }
    currentMode = coordSystemMode;
    if (currentMode != MassPropertiesMode::Custom) {
        selectingCustomCoordSystem = false;
        clearCurrentDatumObject();
        hasCurrentDatumPlacement = false;
        panel->ui.customEdit->clear();
    }
    if (Gui::Selection()
            .getSelection(
                targetDocumentName.c_str(),
                Gui::ResolveMode::NoResolve
            )
            .empty()) {
        clearUiFields();
        panel->ui.objectList->clear();
        removeTemporaryObjects();
        return;
    }

    updateInertiaVisibility();
    tryUpdate();
}

void TaskMassProperties::saveResult()
{
    App::Document* doc = targetDocument();
    if (!doc || !previewTransaction
        || !previewTransaction->ownsCurrentTransaction()) {
        return;
    }
    try {
        // Re-read every selected source immediately before the durable
        // transaction. Geometry, placements, material density, and History
        // position may all have changed while the modeless preview was open.
        tryUpdate();
    }
    catch (const Base::Exception& error) {
        Base::Console().error(
            "Mass properties could not refresh its sources: %s\n",
            error.what()
        );
        return;
    }
    catch (const std::exception& error) {
        Base::Console().error(
            "Mass properties could not refresh its sources: %s\n",
            error.what()
        );
        return;
    }
    auto* datum = currentDatumObject();

    if (panel->ui.objectList->count() == 0
        || (currentMode == MassPropertiesMode::Custom && !datum)) {
        return;
    }

    std::unique_ptr<OwnedMassPropertiesTransaction> transaction;
    try {
    if (!abortPreviewTransaction()) {
        return;
    }
    transaction = std::make_unique<OwnedMassPropertiesTransaction>(
        *doc,
        "Add Mass Properties"
    );

    auto group = freecad_cast<App::DocumentObjectGroup*>(doc->getObject("Measurements"));

    if (!group || !group->isValid()) {
        group = doc->addObject<App::DocumentObjectGroup>("Measurements");
    }

    auto* obj = doc->addObject("Measure::Result", "MassProperties");
    if (!obj) {
        transaction.reset();
        startPreviewTransaction();
        update(Gui::SelectionChanges());
        return;
    }

    obj->Visibility.setValue(true);

    std::vector<App::DocumentObject*> sourceObjects;
    std::vector<std::string> sourceSubNames;
    std::vector<Base::Placement> sourceParentPlacements;
    std::vector<App::DocumentObject*> occurrenceRoots;
    std::vector<std::string> occurrenceSubNames;
    std::vector<App::DocumentObject*> occurrenceDependencies;
    std::unordered_set<App::DocumentObject*> dependencySet;
    if (objectsToMeasure.size() != objectOccurrences.size()) {
        throw Base::RuntimeError(
            "Mass-properties sources lost their occurrence identity"
        );
    }
    sourceObjects.reserve(objectsToMeasure.size());
    sourceSubNames.reserve(objectsToMeasure.size());
    sourceParentPlacements.reserve(objectsToMeasure.size());
    occurrenceRoots.reserve(objectsToMeasure.size());
    occurrenceSubNames.reserve(objectsToMeasure.size());
    auto addDependency = [&](App::DocumentObject* dependency) {
        if (dependency && dependency->getDocument() == doc
            && dependencySet.insert(dependency).second) {
            occurrenceDependencies.push_back(dependency);
        }
    };
    for (std::size_t index = 0;
         index < objectsToMeasure.size();
         ++index) {
        const auto& input = objectsToMeasure[index];
        const auto& tracked = objectOccurrences[index];
        auto* source = input.source ? input.source : input.object;
        if (!source || source->getDocument() != doc
            || !source->getNameInDocument()
            || !doc->containsObject(source)
            || doc->getObject(source->getNameInDocument()) != source
            || doc->getObjectByID(source->getID()) != source
            || !isTimelineSelectionActive(source)) {
            throw Base::ValueError(
                "Mass properties can only save exact current sources "
                "from the result document"
            );
        }

        if (!tracked.root || tracked.root->getDocument() != doc
            || !tracked.root->getNameInDocument()
            || !doc->containsObject(tracked.root)
            || doc->getObject(tracked.root->getNameInDocument())
                != tracked.root
            || doc->getObjectByID(tracked.root->getID())
                != tracked.root
            || !isTimelineSelectionActive(tracked.root)) {
            throw Base::ValueError(
                "A mass-properties occurrence root changed identity"
            );
        }

        Measure::Internal::ResolvedOccurrence resolvedOccurrence;
        if (!Measure::Internal::resolveShapeOccurrence(
                tracked.root,
                tracked.subName,
                resolvedOccurrence
            )
            || !Measure::Internal::endpointRepresentsSource(
                resolvedOccurrence.endpoint,
                source
            )) {
            throw Base::ValueError(
                "A mass-properties occurrence no longer resolves to "
                "its selected source"
            );
        }

        sourceObjects.push_back(source);
        sourceSubNames.push_back(input.sourceSubName);
        sourceParentPlacements.push_back(
            input.sourceParentPlacement
        );
        occurrenceRoots.push_back(tracked.root);
        occurrenceSubNames.push_back(tracked.subName);
        for (auto* dependency : resolvedOccurrence.members) {
            addDependency(dependency);
        }
        addDependency(source);
    }
    if (sourceObjects.empty()) {
        throw Base::ValueError(
            "Mass properties require at least one persistent source"
        );
    }

    auto* sourceProperty =
        dynamic_cast<App::PropertyLinkSubList*>(
            obj->addDynamicProperty(
                "App::PropertyLinkSubListGlobal",
                "MassPropertySources",
                "Sources",
                "Exact geometry occurrences used by this result"
            )
        );
    auto* parentProperty =
        dynamic_cast<App::PropertyPlacementList*>(
            obj->addDynamicProperty(
                "App::PropertyPlacementList",
                "MassPropertySourceParents",
                "Sources",
                "Occurrence transforms relative to each source",
                App::Prop_None,
                true
            )
        );
    auto* occurrenceProperty =
        dynamic_cast<App::PropertyLinkSubList*>(
            obj->addDynamicProperty(
                "App::PropertyLinkSubListGlobal",
                "MassPropertyOccurrences",
                "Sources",
                "Live root and subobject paths for measured occurrences",
                App::Prop_Hidden,
                true,
                true
            )
        );
    auto* dependencyProperty =
        dynamic_cast<App::PropertyLinkList*>(
            obj->addDynamicProperty(
                "App::PropertyLinkListGlobal",
                "MassPropertyOccurrenceDependencies",
                "Sources",
                "Objects whose transforms affect measured occurrences",
                App::Prop_Hidden,
                true,
                true
            )
        );
    auto* unitsProperty = dynamic_cast<App::PropertyInteger*>(
        obj->addDynamicProperty(
            "App::PropertyInteger",
            "MassPropertyUnitsSchema",
            "Sources",
            "Units schema used to format calculated outputs",
            App::Prop_None,
            true
        )
    );
    if (!sourceProperty || !parentProperty
        || !occurrenceProperty || !dependencyProperty
        || !unitsProperty) {
        throw Base::TypeError(
            "Could not establish the parametric mass-properties inputs"
        );
    }
    sourceProperty->setValues(sourceObjects, sourceSubNames);
    parentProperty->setValues(sourceParentPlacements);
    occurrenceProperty->setValues(
        occurrenceRoots,
        occurrenceSubNames
    );
    unitsProperty->setValue(unitsSchemaIndex);

    if (currentMode == MassPropertiesMode::Custom) {
        if (!datum || datum->getDocument() != doc
            || !datum->getNameInDocument()
            || !doc->containsObject(datum)
            || doc->getObject(datum->getNameInDocument()) != datum
            || doc->getObjectByID(datum->getID()) != datum
            || !isTimelineSelectionActive(datum)) {
            throw Base::ValueError(
                "The custom mass-properties reference changed identity"
            );
        }
        auto* referenceOccurrenceRoot =
            currentDatumOccurrenceRoot();
        const auto referenceMembers =
            referenceOccurrenceRoot
            ? referenceOccurrenceRoot->getSubObjectList(
                  currentDatumOccurrenceSubName.c_str(),
                  nullptr,
                  false
              )
            : std::vector<App::DocumentObject*> {};
        if (!referenceOccurrenceRoot || referenceMembers.empty()
            || !Measure::Internal::endpointRepresentsSource(
                referenceMembers.back(),
                datum
            )) {
            throw Base::ValueError(
                "The custom mass-properties reference occurrence "
                "changed identity"
            );
        }
        auto* referenceProperty =
            dynamic_cast<App::PropertyLink*>(
                obj->addDynamicProperty(
                    "App::PropertyLinkGlobal",
                    "MassPropertyReference",
                    "Sources",
                    "Coordinate-system or axis reference"
                )
            );
        auto* referenceOccurrence =
            dynamic_cast<App::PropertyLinkSub*>(
                obj->addDynamicProperty(
                    "App::PropertyLinkSubGlobal",
                    "MassPropertyReferenceOccurrence",
                    "Sources",
                    "Live root and subobject path for the reference",
                    App::Prop_Hidden,
                    true,
                    true
                )
            );
        auto* referenceParent =
            dynamic_cast<App::PropertyPlacement*>(
                obj->addDynamicProperty(
                    "App::PropertyPlacement",
                    "MassPropertyReferenceParent",
                    "Sources",
                    "Occurrence transform relative to the reference",
                    App::Prop_None,
                    true
                )
            );
        auto* hasReference = dynamic_cast<App::PropertyBool*>(
            obj->addDynamicProperty(
                "App::PropertyBool",
                "MassPropertyHasReference",
                "Sources",
                "Whether the saved reference placement is authoritative",
                App::Prop_None,
                true
            )
        );
        if (!referenceProperty || !referenceOccurrence
            || !referenceParent
            || !hasReference) {
            throw Base::TypeError(
                "Could not establish the mass-properties reference"
            );
        }
        Base::Placement datumPlacement;
        if (const auto* placement =
                dynamic_cast<const App::PropertyPlacement*>(
                    datum->getPropertyByName("Placement")
                )) {
            datumPlacement = placement->getValue();
        }
        const Base::Placement liveDatumPlacement =
            App::GeoFeature::getGlobalPlacement(
                datum,
                referenceOccurrenceRoot,
                currentDatumOccurrenceSubName
            );
        referenceProperty->setValue(datum);
        referenceOccurrence->setValue(
            referenceOccurrenceRoot,
            std::vector<std::string> {
                currentDatumOccurrenceSubName,
            }
        );
        referenceParent->setValue(
            liveDatumPlacement * datumPlacement.inverse()
        );
        hasReference->setValue(hasCurrentDatumPlacement);
        for (auto* dependency : referenceMembers) {
            addDependency(dependency);
        }
        addDependency(datum);
    }
    dependencyProperty->setValues(occurrenceDependencies);

    auto setQuantity = [&](const char* name, const char* group, const Base::Quantity& quantity) {
        Base::Quantity q(quantity);
        if (std::fabs(q.getValue()) < Base::Precision::Confusion()) {
            q.setValue(0.0);
        }
        auto* prop = freecad_cast<App::PropertyString*>(
            obj->addDynamicProperty(
                "App::PropertyString",
                name,
                group,
                "Calculated mass-property value",
                App::Prop_Output
            )
        );
        if (prop) {
            std::string text;
            auto schema = Base::UnitsApi::createSchema(static_cast<std::size_t>(unitsSchemaIndex));
            if (schema) {
                text = schema->translate(q);
            }
            else {
                text = Base::UnitsApi::schemaTranslate(q);
            }

            prop->setValue(text.c_str());
            prop->setReadOnly(true);
        }
    };

    auto setVector = [&](const char* name, const char* group, Base::Vector3d& value) {
        for (int i = 0; i < 3; ++i) {
            if (value[i] < Base::Precision::Confusion() && value[i] > -Base::Precision::Confusion()) {
                value[i] = 0.0;
            }
        }
        auto* prop = freecad_cast<App::PropertyVector*>(
            obj->addDynamicProperty(
                "App::PropertyVector",
                name,
                group,
                "Calculated mass-property direction",
                App::Prop_Output
            )
        );
        if (prop) {
            prop->setValue(value);
            prop->setReadOnly(true);
        }
    };

    auto setString = [&](const char* name, const char* group, const std::string& value) {
        auto* prop = freecad_cast<App::PropertyString*>(
            obj->getPropertyByName(name)
        );
        if (!prop) {
            prop = freecad_cast<App::PropertyString*>(
                obj->addDynamicProperty(
                    "App::PropertyString",
                    name,
                    group
                )
            );
        }
        if (prop) {
            prop->setValue(value);
            prop->setReadOnly(true);
        }
    };

    setString(
        "Mode",
        "Parameters",
        currentMode == MassPropertiesMode::Custom ? "Custom" : "Center of gravity"
    );

    setQuantity("Volume", "Physical Properties", currentInfo.volume);
    setQuantity("Mass", "Physical Properties", currentInfo.mass);
    setQuantity("Density", "Physical Properties", currentInfo.density);
    setQuantity("SurfaceArea", "Physical Properties", currentInfo.surfaceArea);

    setQuantity(
        "CenterOfGravityX",
        "Center of Gravity",
        Base::Quantity(currentInfo.cog.x, Base::Unit::Length)
    );
    setQuantity(
        "CenterOfGravityY",
        "Center of Gravity",
        Base::Quantity(currentInfo.cog.y, Base::Unit::Length)
    );
    setQuantity(
        "CenterOfGravityZ",
        "Center of Gravity",
        Base::Quantity(currentInfo.cog.z, Base::Unit::Length)
    );
    setQuantity(
        "CenterOfVolumeX",
        "Center of Volume",
        Base::Quantity(currentInfo.cov.x, Base::Unit::Length)
    );
    setQuantity(
        "CenterOfVolumeY",
        "Center of Volume",
        Base::Quantity(currentInfo.cov.y, Base::Unit::Length)
    );
    setQuantity(
        "CenterOfVolumeZ",
        "Center of Volume",
        Base::Quantity(currentInfo.cov.z, Base::Unit::Length)
    );

    const bool hasAxisSelection = currentMode == MassPropertiesMode::Custom && datum
        && datum->isDerivedFrom<App::Line>();

    if (hasAxisSelection) {
        setQuantity(
            "AxisInertia",
            "Inertia",
            Base::Quantity(currentInfo.axisInertia, Base::Unit::Inertia)
        );
    }
    else {
        setQuantity(
            "InertiaJox",
            "Inertia",
            Base::Quantity(currentInfo.inertiaJo.x, Base::Unit::Inertia)
        );
        setQuantity(
            "InertiaJoy",
            "Inertia",
            Base::Quantity(currentInfo.inertiaJo.y, Base::Unit::Inertia)
        );
        setQuantity(
            "InertiaJoz",
            "Inertia",
            Base::Quantity(currentInfo.inertiaJo.z, Base::Unit::Inertia)
        );
        setQuantity(
            "InertiaJxy",
            "Inertia",
            Base::Quantity(currentInfo.inertiaJCross.x, Base::Unit::Inertia)
        );
        setQuantity(
            "InertiaJzx",
            "Inertia",
            Base::Quantity(currentInfo.inertiaJCross.y, Base::Unit::Inertia)
        );
        setQuantity(
            "InertiaJzy",
            "Inertia",
            Base::Quantity(currentInfo.inertiaJCross.z, Base::Unit::Inertia)
        );
        setQuantity("InertiaJx", "Inertia", Base::Quantity(currentInfo.inertiaJ.x, Base::Unit::Inertia));
        setQuantity("InertiaJy", "Inertia", Base::Quantity(currentInfo.inertiaJ.y, Base::Unit::Inertia));
        setQuantity("InertiaJz", "Inertia", Base::Quantity(currentInfo.inertiaJ.z, Base::Unit::Inertia));

        setVector("PrincipalAxis1", "Inertia", currentInfo.principalAxis1);
        setVector("PrincipalAxis2", "Inertia", currentInfo.principalAxis2);
        setVector("PrincipalAxis3", "Inertia", currentInfo.principalAxis3);
    }

    if (group) {
        group->addObject(obj);
        group->purgeTouched();
    }

    if (auto* guiDoc = Gui::Application::Instance
            ? Gui::Application::Instance->getDocument(doc)
            : nullptr) {
        if (auto* view = dynamic_cast<Gui::ViewProviderDocumentObject*>(guiDoc->getViewProvider(obj))) {
            if (auto* resultView = dynamic_cast<ViewProviderMassPropertiesResult*>(view)) {
                resultView->setCenters(currentInfo.cog, currentInfo.cov);
                resultView->setPrincipalAxes(
                    currentInfo.cog,
                    currentInfo.principalAxis1,
                    currentInfo.principalAxis2,
                    currentInfo.principalAxis3,
                    !hasAxisSelection
                );
            }
            view->setShowable(true);
            view->show();
        }
    }

    doc->recompute();
    if (!finishDurableResult(std::move(transaction))) {
        startPreviewTransaction();
        update(Gui::SelectionChanges());
    }
    }
    catch (const Base::Exception& error) {
        Base::Console().error(
            "Saving mass properties failed: %s\n",
            error.what()
        );
        transaction.reset();
        startPreviewTransaction();
        update(Gui::SelectionChanges());
    }
    catch (const std::exception& error) {
        Base::Console().error(
            "Saving mass properties failed: %s\n",
            error.what()
        );
        transaction.reset();
        startPreviewTransaction();
        update(Gui::SelectionChanges());
    }
}
