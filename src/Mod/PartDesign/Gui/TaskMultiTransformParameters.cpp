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


#include <QAction>
#include <QByteArray>
#include <QSignalBlocker>
#include <QTimer>

#include <algorithm>
#include <exception>
#include <sstream>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <App/DocumentObject.h>
#include <App/Origin.h>
#include <App/TransactionDefs.h>

#include <Base/Console.h>
#include <Base/Exception.h>
#include <Gui/Application.h>
#include <Gui/Selection/Selection.h>
#include <Gui/Command.h>
#include <Mod/PartDesign/App/Body.h>
#include <Mod/PartDesign/App/FeatureLinearPattern.h>
#include <Mod/PartDesign/App/FeatureMirrored.h>
#include <Mod/PartDesign/App/FeatureMultiTransform.h>
#include <Mod/PartDesign/App/FeaturePolarPattern.h>
#include <Mod/PartDesign/App/FeatureScaled.h>

#include "ui_TaskMultiTransformParameters.h"
#include "TaskMultiTransformParameters.h"
#include "TaskMirroredParameters.h"
#include "TaskPatternParameters.h"
#include "TaskScaledParameters.h"
#include "Utils.h"

using namespace PartDesignGui;
using namespace Gui;

/* TRANSLATOR PartDesignGui::TaskMultiTransformParameters */

namespace
{
enum TransformationItemRole
{
    DocumentNameRole = Qt::UserRole,
    ObjectNameRole,
    ObjectIdRole,
    PropertyIndexRole,
};

bool itemMatchesObject(
    const QListWidgetItem* item,
    const App::DocumentObject* object,
    const std::size_t propertyIndex
)
{
    if (!item || !object || !object->isAttachedToDocument()
        || !object->getNameInDocument()) {
        return false;
    }

    return item->data(DocumentNameRole).toString()
            == QString::fromLatin1(object->getDocument()->getName())
        && item->data(ObjectNameRole).toString()
            == QString::fromLatin1(object->getNameInDocument())
        && item->data(ObjectIdRole).toLongLong() == object->getID()
        && item->data(PropertyIndexRole).toULongLong() == propertyIndex;
}

void setItemIdentity(
    QListWidgetItem* item,
    const App::DocumentObject* object,
    const std::size_t propertyIndex
)
{
    item->setData(
        DocumentNameRole,
        QString::fromLatin1(object->getDocument()->getName())
    );
    item->setData(
        ObjectNameRole,
        QString::fromLatin1(object->getNameInDocument())
    );
    item->setData(ObjectIdRole, static_cast<qlonglong>(object->getID()));
    item->setData(
        PropertyIndexRole,
        static_cast<qulonglong>(propertyIndex)
    );
}

template<typename FeatureT>
FeatureT* createTransformationExact(
    PartDesign::Body* body,
    const char* typeName,
    const std::string& featureName
)
{
    if (!body || !body->isAttachedToDocument()
        || !body->getNameInDocument() || !typeName
        || !*typeName || featureName.empty()) {
        return nullptr;
    }

    auto* document = body->getDocument();
    if (!document || document->getObject(body->getNameInDocument()) != body
        || document->getObjectByID(body->getID()) != body) {
        return nullptr;
    }

    try {
        std::ostringstream factory;
        factory << Gui::Command::getObjectCmd(body)
                << ".newObject('" << typeName << "','"
                << featureName << "')";
        auto* feature = freecad_cast<FeatureT*>(
            Gui::Command::runDocumentObjectCommand(
                Gui::Command::Doc,
                *document,
                QByteArray(factory.str().c_str()),
                FeatureT::getClassTypeId()
            )
        );
        if (!feature || feature->getDocument() != document
            || !feature->getNameInDocument()
            || document->getObject(feature->getNameInDocument()) != feature
            || document->getObjectByID(feature->getID()) != feature
            || !body->hasObject(feature)) {
            Base::Console().error(
                "The MultiTransform factory did not return the exact "
                "transformation owned by its Body\n"
            );
            return nullptr;
        }
        return feature;
    }
    catch (const Base::Exception& error) {
        Base::Console().error(
            "Could not create the exact MultiTransform child: %s\n",
            error.what()
        );
    }
    catch (const std::exception& error) {
        Base::Console().error(
            "Could not create the exact MultiTransform child: %s\n",
            error.what()
        );
    }
    catch (...) {
        Base::Console().error(
            "Could not create the exact MultiTransform child\n"
        );
    }
    return nullptr;
}

void abortCreationTransaction(App::Document* document, const int transactionId)
{
    if (document && transactionId != App::NullTransaction
        && document->getBookedTransactionID() == transactionId) {
        document->abortTransaction();
    }
}
}

TaskMultiTransformParameters::TaskMultiTransformParameters(
    ViewProviderTransformed* TransformedView,
    QWidget* parent
)
    : TaskTransformedParameters(TransformedView, parent)
    , ui(new Ui_TaskMultiTransformParameters)
{
    if (auto* multiTransform =
            TransformedView
            ? TransformedView->getObject<PartDesign::MultiTransform>()
            : nullptr;
        multiTransform && multiTransform->isAttachedToDocument()
        && multiTransform->getNameInDocument()) {
        multiTransformDocumentName = multiTransform->getDocument()->getName();
        multiTransformObjectName = multiTransform->getNameInDocument();
        multiTransformObjectId = multiTransform->getID();
    }
    setupUI();
}

void TaskMultiTransformParameters::setupParameterUI(QWidget* widget)
{
    ui->setupUi(widget);
    QMetaObject::connectSlotsByName(this);

    // Create a context menu for the listview of transformation features
    editAction = new QAction(tr("Edit"), ui->listTransformFeatures);
    editAction->connect(
        editAction,
        &QAction::triggered,
        this,
        &TaskMultiTransformParameters::onTransformEdit
    );
    ui->listTransformFeatures->addAction(editAction);
    deleteAction = new QAction(tr("Delete"), ui->listTransformFeatures);
    deleteAction->connect(
        deleteAction,
        &QAction::triggered,
        this,
        &TaskMultiTransformParameters::onTransformDelete
    );
    ui->listTransformFeatures->addAction(deleteAction);
    addMirroredAction =
        new QAction(tr("Add Mirror Transformation"), ui->listTransformFeatures);
    addMirroredAction->connect(
        addMirroredAction,
        &QAction::triggered,
        this,
        &TaskMultiTransformParameters::onTransformAddMirrored
    );
    ui->listTransformFeatures->addAction(addMirroredAction);
    addLinearAction = new QAction(tr("Add Linear Pattern"), ui->listTransformFeatures);
    addLinearAction->connect(
        addLinearAction,
        &QAction::triggered,
        this,
        &TaskMultiTransformParameters::onTransformAddLinearPattern
    );
    ui->listTransformFeatures->addAction(addLinearAction);
    addPolarAction = new QAction(tr("Add Polar Pattern"), ui->listTransformFeatures);
    addPolarAction->connect(
        addPolarAction,
        &QAction::triggered,
        this,
        &TaskMultiTransformParameters::onTransformAddPolarPattern
    );
    ui->listTransformFeatures->addAction(addPolarAction);
    addScaledAction =
        new QAction(tr("Add Scale Transformation"), ui->listTransformFeatures);
    addScaledAction->connect(
        addScaledAction,
        &QAction::triggered,
        this,
        &TaskMultiTransformParameters::onTransformAddScaled
    );
    ui->listTransformFeatures->addAction(addScaledAction);
    moveUpAction = new QAction(tr("Move Up"), ui->listTransformFeatures);
    moveUpAction->connect(
        moveUpAction,
        &QAction::triggered,
        this,
        &TaskMultiTransformParameters::onMoveUp
    );
    ui->listTransformFeatures->addAction(moveUpAction);
    moveDownAction = new QAction(tr("Move Down"), ui->listTransformFeatures);
    moveDownAction->connect(
        moveDownAction,
        &QAction::triggered,
        this,
        &TaskMultiTransformParameters::onMoveDown
    );
    ui->listTransformFeatures->addAction(moveDownAction);
    ui->listTransformFeatures->setContextMenuPolicy(Qt::ActionsContextMenu);

    connect(
        ui->listTransformFeatures,
        &QListWidget::activated,
        this,
        &TaskMultiTransformParameters::onTransformActivated
    );
    connect(
        ui->listTransformFeatures,
        &QListWidget::currentRowChanged,
        this,
        [this](int) {
            updateOperationState();
        }
    );

    connect(ui->buttonOK, &QToolButton::pressed, this, &TaskMultiTransformParameters::onSubTaskButtonOK);
    ui->buttonOK->hide();

    rebuildTransformList();
}

void TaskMultiTransformParameters::retranslateParameterUI(QWidget* widget)
{
    ui->retranslateUi(widget);
}

PartDesign::MultiTransform* TaskMultiTransformParameters::resolveMultiTransform()
{
    if (multiTransformDocumentName.empty() || multiTransformObjectName.empty()
        || multiTransformObjectId < 0) {
        TransformedView = nullptr;
        return nullptr;
    }

    App::Document* document = nullptr;
    try {
        document = App::GetApplication().getDocument(
            multiTransformDocumentName.c_str()
        );
    }
    catch (...) {
    }
    auto* object = document
        ? document->getObjectByID(multiTransformObjectId)
        : nullptr;
    auto* multiTransform = freecad_cast<PartDesign::MultiTransform*>(object);
    if (!multiTransform || !multiTransform->getNameInDocument()
        || multiTransformObjectName != multiTransform->getNameInDocument()) {
        TransformedView = nullptr;
        return nullptr;
    }

    TransformedView = Gui::Application::Instance
        ? Gui::Application::Instance
              ->getViewProvider<ViewProviderTransformed>(multiTransform)
        : nullptr;
    return multiTransform;
}

PartDesign::Transformed* TaskMultiTransformParameters::resolveSubFeature() const
{
    if (subFeatureDocumentName.empty() || subFeatureObjectName.empty()
        || subFeatureObjectId < 0) {
        return nullptr;
    }

    App::Document* document = nullptr;
    try {
        document =
            App::GetApplication().getDocument(subFeatureDocumentName.c_str());
    }
    catch (...) {
    }
    auto* object = document ? document->getObjectByID(subFeatureObjectId) : nullptr;
    auto* transformation = freecad_cast<PartDesign::Transformed*>(object);
    return transformation && transformation->getNameInDocument()
            && subFeatureObjectName == transformation->getNameInDocument()
        ? transformation
        : nullptr;
}

void TaskMultiTransformParameters::rememberSubFeature(
    PartDesign::Transformed* transformation
)
{
    subFeature = transformation;
    if (!transformation || !transformation->isAttachedToDocument()
        || !transformation->getNameInDocument()) {
        subFeatureDocumentName.clear();
        subFeatureObjectName.clear();
        subFeatureObjectId = -1;
        return;
    }
    subFeatureDocumentName = transformation->getDocument()->getName();
    subFeatureObjectName = transformation->getNameInDocument();
    subFeatureObjectId = transformation->getID();
}

bool TaskMultiTransformParameters::isLiveTransformation(
    const PartDesign::MultiTransform* multiTransform,
    const App::DocumentObject* object
) const
{
    return multiTransform && object && object->isAttachedToDocument()
        && object->getDocument() == multiTransform->getDocument()
        && object->getNameInDocument()
        && object->getDocument()->getObjectByID(object->getID()) == object;
}

bool TaskMultiTransformParameters::isOwnedTransformation(
    const PartDesign::MultiTransform* multiTransform,
    const PartDesign::Transformed* transformation
) const
{
    if (!isLiveTransformation(multiTransform, transformation)
        || transformation == multiTransform) {
        return false;
    }
    auto* ownerBody = Part::BodyBase::findBodyOf(
        const_cast<PartDesign::MultiTransform*>(multiTransform)
    );
    return ownerBody
        && Part::BodyBase::findBodyOf(
               const_cast<PartDesign::Transformed*>(transformation)
           )
            == ownerBody;
}

bool TaskMultiTransformParameters::transformListMatches(
    const PartDesign::MultiTransform* multiTransform
) const
{
    if (!multiTransform || !ui->listTransformFeatures) {
        return false;
    }

    const auto transformations = multiTransform->Transformations.getValues();
    int row = 0;
    for (std::size_t index = 0; index < transformations.size(); ++index) {
        auto* transformation = transformations[index];
        if (!isLiveTransformation(multiTransform, transformation)) {
            continue;
        }
        auto* item = ui->listTransformFeatures->item(row++);
        if (!itemMatchesObject(item, transformation, index)
            || item->text()
                != QString::fromUtf8(transformation->Label.getValue())) {
            return false;
        }
    }

    if (row == 0) {
        return editHint && ui->listTransformFeatures->count() == 1;
    }
    return !editHint && row == ui->listTransformFeatures->count();
}

void TaskMultiTransformParameters::rebuildTransformList(
    const long preferredObjectId
)
{
    auto* multiTransform = resolveMultiTransform();
    const QSignalBlocker blocker(ui->listTransformFeatures);
    ui->listTransformFeatures->clear();

    int preferredRow = -1;
    if (multiTransform) {
        const auto transformations =
            multiTransform->Transformations.getValues();
        for (std::size_t index = 0; index < transformations.size(); ++index) {
            auto* transformation = transformations[index];
            if (!isLiveTransformation(multiTransform, transformation)) {
                continue;
            }
            auto* item = new QListWidgetItem(
                QString::fromUtf8(transformation->Label.getValue())
            );
            setItemIdentity(item, transformation, index);
            ui->listTransformFeatures->addItem(item);
            if (transformation->getID() == preferredObjectId) {
                preferredRow = ui->listTransformFeatures->count() - 1;
            }
        }
    }

    editHint = ui->listTransformFeatures->count() == 0;
    if (editHint) {
        ui->listTransformFeatures->addItem(
            tr("Right-click to add a transformation")
        );
    }
    else {
        ui->listTransformFeatures->setCurrentRow(
            preferredRow >= 0 ? preferredRow : 0,
            QItemSelectionModel::ClearAndSelect
        );
    }
    ui->listTransformFeatures->setEnabled(multiTransform != nullptr);
    updateOperationState();
}

PartDesign::Transformed*
TaskMultiTransformParameters::transformationForRow(
    PartDesign::MultiTransform* multiTransform,
    const int row,
    const std::vector<App::DocumentObject*>& transformations,
    std::size_t& propertyIndex
) const
{
    if (!multiTransform || row < 0 || row >= ui->listTransformFeatures->count()
        || editHint) {
        return nullptr;
    }
    auto* item = ui->listTransformFeatures->item(row);
    if (!item) {
        return nullptr;
    }

    bool indexValid = false;
    const auto storedIndex =
        item->data(PropertyIndexRole).toULongLong(&indexValid);
    if (!indexValid || storedIndex >= transformations.size()) {
        return nullptr;
    }
    propertyIndex = static_cast<std::size_t>(storedIndex);
    auto* object = transformations[propertyIndex];
    if (!isLiveTransformation(multiTransform, object)
        || !itemMatchesObject(item, object, propertyIndex)) {
        return nullptr;
    }
    return freecad_cast<PartDesign::Transformed*>(object);
}

void TaskMultiTransformParameters::updateOperationState()
{
    auto* multiTransform = resolveMultiTransform();
    const bool synchronized =
        multiTransform && transformListMatches(multiTransform);
    const auto transformations = synchronized
        ? multiTransform->Transformations.getValues()
        : std::vector<App::DocumentObject*> {};
    std::size_t propertyIndex = 0;
    auto* selected = synchronized
        ? transformationForRow(
              multiTransform,
              ui->listTransformFeatures->currentRow(),
              transformations,
              propertyIndex
          )
        : nullptr;
    const bool owned =
        selected && isOwnedTransformation(multiTransform, selected);
    const bool editable = owned
        && (selected->is<PartDesign::Mirrored>()
            || selected->is<PartDesign::LinearPattern>()
            || selected->is<PartDesign::PolarPattern>()
            || selected->is<PartDesign::Scaled>());
    const int row = ui->listTransformFeatures->currentRow();
    const int liveRows = editHint ? 0 : ui->listTransformFeatures->count();

    if (editAction) {
        editAction->setEnabled(editable);
    }
    if (deleteAction) {
        deleteAction->setEnabled(owned);
    }
    if (moveUpAction) {
        moveUpAction->setEnabled(owned && row > 0);
    }
    if (moveDownAction) {
        moveDownAction->setEnabled(owned && row >= 0 && row + 1 < liveRows);
    }

    auto* body = multiTransform
        ? Part::BodyBase::findBodyOf(multiTransform)
        : nullptr;
    const bool canAdd = multiTransform && body;
    for (auto* action : {
             addMirroredAction,
             addLinearAction,
             addPolarAction,
             addScaledAction,
         }) {
        if (action) {
            action->setEnabled(canAdd);
        }
    }
}

bool TaskMultiTransformParameters::ensureTransformListSynchronized()
{
    auto* multiTransform = resolveMultiTransform();
    if (!multiTransform || !transformListMatches(multiTransform)) {
        closeSubTask();
        rebuildTransformList();
        return false;
    }
    updateOperationState();
    return true;
}

void TaskMultiTransformParameters::scheduleTransformListRefresh()
{
    if (refreshScheduled) {
        return;
    }
    refreshScheduled = true;
    QTimer::singleShot(0, this, [this]() {
        refreshScheduled = false;
        updateUI();
    });
}

void TaskMultiTransformParameters::updateUI()
{
    auto* multiTransform = resolveMultiTransform();
    if (!multiTransform || !transformListMatches(multiTransform)) {
        const auto* current = ui->listTransformFeatures->currentItem();
        const long preferredId = current
            ? current->data(ObjectIdRole).toLongLong()
            : -1;
        closeSubTask();
        rebuildTransformList(preferredId);
        return;
    }
    updateOperationState();
}

void TaskMultiTransformParameters::recomputeMultiTransform(
    PartDesign::MultiTransform* multiTransform
)
{
    if (!multiTransform || resolveMultiTransform() != multiTransform) {
        return;
    }
    if (TransformedView) {
        TransformedView->recomputeFeature();
    }
    else {
        multiTransform->recomputeFeature(true);
    }
}

void TaskMultiTransformParameters::slotDeletedObject(const Gui::ViewProviderDocumentObject& Obj)
{
    auto* deleted = Obj.getObject();
    if (deleted && deleted->getID() == subFeatureObjectId
        && deleted->getNameInDocument()
        && subFeatureObjectName == deleted->getNameInDocument()
        && deleted->getDocument()
        && subFeatureDocumentName == deleted->getDocument()->getName()) {
        rememberSubFeature(nullptr);
        ui->buttonOK->hide();
    }
    TaskTransformedParameters::slotDeletedObject(Obj);
    scheduleTransformListRefresh();
}

void TaskMultiTransformParameters::slotChangedObject(
    const Gui::ViewProviderDocumentObject& Obj,
    const App::Property& Prop
)
{
    auto* object = Obj.getObject();
    if (object && object->getID() == multiTransformObjectId
        && object->getNameInDocument()
        && multiTransformObjectName == object->getNameInDocument()
        && object->getDocument()
        && multiTransformDocumentName == object->getDocument()->getName()) {
        auto* multiTransform =
            freecad_cast<PartDesign::MultiTransform*>(object);
        if (multiTransform && &Prop == &multiTransform->Transformations) {
            scheduleTransformListRefresh();
        }
    }
}

void TaskMultiTransformParameters::slotRelabelObject(
    const Gui::ViewProviderDocumentObject& Obj
)
{
    Q_UNUSED(Obj);
    scheduleTransformListRefresh();
}

void TaskMultiTransformParameters::closeSubTask()
{
    if (subTask) {
        ui->buttonOK->hide();
        exitSelectionMode();
        // Apply only while this exact object is still a live child of this
        // MultiTransform.  A property edit, undo, or deletion can otherwise
        // leave the nested panel pointing at an unrelated/recreated object.
        auto* multiTransform = resolveMultiTransform();
        auto* liveSubFeature = resolveSubFeature();
        const auto transformations = multiTransform
            ? multiTransform->Transformations.getValues()
            : std::vector<App::DocumentObject*> {};
        if (liveSubFeature
            && isOwnedTransformation(multiTransform, liveSubFeature)
            && std::ranges::find(transformations, liveSubFeature)
                != transformations.end()) {
            subTask->apply();
        }

        delete subTask;
        subTask = nullptr;
        rememberSubFeature(nullptr);

        // Remove all parameter ui widgets and layout
        ui->subFeatureWidget->setUpdatesEnabled(false);
        qDeleteAll(ui->subFeatureWidget->findChildren<QWidget*>(QString(), Qt::FindDirectChildrenOnly));
        qDeleteAll(ui->subFeatureWidget->findChildren<QLayout*>(QString(), Qt::FindDirectChildrenOnly));
        ui->subFeatureWidget->setUpdatesEnabled(true);
    }
}

void TaskMultiTransformParameters::onTransformDelete()
{
    if (!ensureTransformListSynchronized() || editHint) {
        return;
    }
    auto* multiTransform = resolveMultiTransform();
    auto transformations = multiTransform->Transformations.getValues();
    std::size_t propertyIndex = 0;
    auto* feature = transformationForRow(
        multiTransform,
        ui->listTransformFeatures->currentRow(),
        transformations,
        propertyIndex
    );
    if (!feature || !isOwnedTransformation(multiTransform, feature)) {
        updateOperationState();
        return;
    }

    const long featureId = feature->getID();
    const std::string featureName = feature->getNameInDocument();
    if (feature == resolveSubFeature()) {
        rememberSubFeature(nullptr);
    }
    closeSubTask();

    setupTransaction();
    multiTransform = resolveMultiTransform();
    if (!multiTransform) {
        return;
    }
    transformations = multiTransform->Transformations.getValues();
    if (propertyIndex >= transformations.size()
        || transformations[propertyIndex] != feature
        || multiTransform->getDocument()->getObjectByID(featureId) != feature) {
        rebuildTransformList();
        return;
    }

    transformations.erase(transformations.begin() + propertyIndex);
    multiTransform->Transformations.setValues(transformations);
    if (auto* exactFeature =
            multiTransform->getDocument()->getObjectByID(featureId);
        exactFeature && exactFeature->getNameInDocument()
        && featureName == exactFeature->getNameInDocument()) {
        multiTransform->getDocument()->removeObject(featureName.c_str());
    }
    // Note: When the last transformation is deleted, recomputeFeature does nothing, because
    // Transformed::execute() says: "No transformations defined, exit silently"
    recomputeMultiTransform(multiTransform);
    rebuildTransformList();
}

void TaskMultiTransformParameters::onTransformEdit()
{
    if (!ensureTransformListSynchronized() || editHint) {
        return;
    }
    closeSubTask();  // For example if user is editing one subTask and then double-clicks on another
                     // without OK'ing first
    auto* multiTransform = resolveMultiTransform();
    if (!multiTransform || !transformListMatches(multiTransform)) {
        rebuildTransformList();
        return;
    }
    const auto transformations = multiTransform->Transformations.getValues();
    std::size_t propertyIndex = 0;
    auto* selected = transformationForRow(
        multiTransform,
        ui->listTransformFeatures->currentRow(),
        transformations,
        propertyIndex
    );
    if (!selected || !isOwnedTransformation(multiTransform, selected)) {
        updateOperationState();
        return;
    }
    rememberSubFeature(selected);

    if (selected->is<PartDesign::Mirrored>()) {
        subTask = new TaskMirroredParameters(this, ui->subFeatureWidget);
    }
    else if (
        selected->is<PartDesign::LinearPattern>()
        || selected->is<PartDesign::PolarPattern>()
    ) {
        subTask = new TaskPatternParameters(this, ui->subFeatureWidget);
    }
    else if (selected->is<PartDesign::Scaled>()) {
        subTask = new TaskScaledParameters(this, ui->subFeatureWidget);
    }
    else {
        rememberSubFeature(nullptr);
        updateOperationState();
        return;
    }

    ui->buttonOK->show();

    subTask->setEnabledTransaction(isEnabledTransaction());
}

void TaskMultiTransformParameters::onTransformActivated(const QModelIndex& index)
{
    if (!index.isValid()) {
        return;
    }
    ui->listTransformFeatures->setCurrentIndex(index);
    onTransformEdit();
}

void TaskMultiTransformParameters::onTransformAddMirrored()
{
    if (!ensureTransformListSynchronized()) {
        return;
    }
    closeSubTask();
    auto* multiTransform = resolveMultiTransform();
    if (!multiTransform) {
        return;
    }
    auto* document = multiTransform->getDocument();
    std::string newFeatName = document->getUniqueObjectName("Mirror");
    auto pcBody = dynamic_cast<PartDesign::Body*>(
        Part::BodyBase::findBodyOf(multiTransform)
    );
    if (!pcBody) {
        return;
    }

    const int creationTransactionId = isEnabledTransaction()
        ? pcBody->getDocument()->openTransaction(
              QT_TRANSLATE_NOOP("Command", "Mirror")
          )
        : App::NullTransaction;

    auto* Feat = createTransformationExact<PartDesign::Mirrored>(
        pcBody,
        "PartDesign::Mirrored",
        newFeatName
    );
    if (!Feat) {
        abortCreationTransaction(document, creationTransactionId);
        return;
    }
    // Gui::Command::updateActive();
    App::DocumentObject* sketch = multiTransform->getSketchObject();
    if (sketch) {
        FCMD_OBJ_CMD(Feat, "MirrorPlane = (" << Gui::Command::getObjectCmd(sketch) << ",['V_Axis'])");
    }
    else {
        App::Origin* orig = pcBody->getOrigin();
        FCMD_OBJ_CMD(Feat, "MirrorPlane = (" << Gui::Command::getObjectCmd(orig->getXY()) << ",[''])");
    }
    const long featureId = Feat->getID();
    finishAdd(Feat);
    // show the new view when no error
    if (auto* liveFeature =
            freecad_cast<PartDesign::Mirrored*>(
                document->getObjectByID(featureId)
            );
        liveFeature && !liveFeature->isError()
        && resolveMultiTransform() == multiTransform) {
        multiTransform->Visibility.setValue(true);
    }
}

void TaskMultiTransformParameters::onTransformAddLinearPattern()
{
    // See CmdPartDesignLinearPattern
    //
    if (!ensureTransformListSynchronized()) {
        return;
    }
    closeSubTask();
    auto* multiTransform = resolveMultiTransform();
    if (!multiTransform) {
        return;
    }
    auto* document = multiTransform->getDocument();
    std::string newFeatName =
        document->getUniqueObjectName("Linear Pattern");
    auto pcBody = dynamic_cast<PartDesign::Body*>(
        Part::BodyBase::findBodyOf(multiTransform)
    );
    if (!pcBody) {
        return;
    }

    const int creationTransactionId = isEnabledTransaction()
        ? pcBody->getDocument()->openTransaction(
              QT_TRANSLATE_NOOP("Command", "Linear Pattern")
          )
        : App::NullTransaction;

    auto* Feat = createTransformationExact<PartDesign::LinearPattern>(
        pcBody,
        "PartDesign::LinearPattern",
        newFeatName
    );
    if (!Feat) {
        abortCreationTransaction(document, creationTransactionId);
        return;
    }
    // Gui::Command::updateActive();
    App::DocumentObject* sketch = multiTransform->getSketchObject();
    if (sketch) {
        FCMD_OBJ_CMD(Feat, "Direction = (" << Gui::Command::getObjectCmd(sketch) << ",['H_Axis'])");
    }
    else {
        // set Direction value before filling up the combo box to avoid creating an empty item
        // inside updateUI()
        auto body =
            dynamic_cast<PartDesign::Body*>(Part::BodyBase::findBodyOf(multiTransform));
        if (body) {
            FCMD_OBJ_CMD(
                Feat,
                "Direction = (" << Gui::Command::getObjectCmd(body->getOrigin()->getX()) << ",[''])"
            );
        }
    }

    FCMD_OBJ_CMD(Feat, "Length = 100");
    FCMD_OBJ_CMD(Feat, "Occurrences = 2");

    const long featureId = Feat->getID();
    finishAdd(Feat);
    // show the new view when no error
    if (auto* liveFeature =
            freecad_cast<PartDesign::LinearPattern*>(
                document->getObjectByID(featureId)
            );
        liveFeature && !liveFeature->isError()
        && resolveMultiTransform() == multiTransform) {
        multiTransform->Visibility.setValue(true);
    }
}

void TaskMultiTransformParameters::onTransformAddPolarPattern()
{
    if (!ensureTransformListSynchronized()) {
        return;
    }
    closeSubTask();
    auto* multiTransform = resolveMultiTransform();
    if (!multiTransform) {
        return;
    }
    auto* document = multiTransform->getDocument();
    std::string newFeatName =
        document->getUniqueObjectName("Polar Pattern");
    auto pcBody = dynamic_cast<PartDesign::Body*>(
        Part::BodyBase::findBodyOf(multiTransform)
    );
    if (!pcBody) {
        return;
    }

    const int creationTransactionId = isEnabledTransaction()
        ? pcBody->getDocument()->openTransaction(
              QT_TRANSLATE_NOOP("Command", "Polar Pattern")
          )
        : App::NullTransaction;

    auto* Feat = createTransformationExact<PartDesign::PolarPattern>(
        pcBody,
        "PartDesign::PolarPattern",
        newFeatName
    );
    if (!Feat) {
        abortCreationTransaction(document, creationTransactionId);
        return;
    }
    // Gui::Command::updateActive();
    App::DocumentObject* sketch = multiTransform->getSketchObject();
    if (sketch) {
        FCMD_OBJ_CMD(Feat, "Axis = (" << Gui::Command::getObjectCmd(sketch) << ",['N_Axis'])");
    }
    else {
        App::Origin* orig = pcBody->getOrigin();
        FCMD_OBJ_CMD(Feat, "Axis = (" << Gui::Command::getObjectCmd(orig->getX()) << ",[''])");
    }
    FCMD_OBJ_CMD(Feat, "Angle = 360");
    FCMD_OBJ_CMD(Feat, "Occurrences = 2");

    const long featureId = Feat->getID();
    finishAdd(Feat);
    // show the new view when no error
    if (auto* liveFeature =
            freecad_cast<PartDesign::PolarPattern*>(
                document->getObjectByID(featureId)
            );
        liveFeature && !liveFeature->isError()
        && resolveMultiTransform() == multiTransform) {
        multiTransform->Visibility.setValue(true);
    }
}

void TaskMultiTransformParameters::onTransformAddScaled()
{
    if (!ensureTransformListSynchronized()) {
        return;
    }
    closeSubTask();
    auto* multiTransform = resolveMultiTransform();
    if (!multiTransform) {
        return;
    }
    auto* document = multiTransform->getDocument();
    std::string newFeatName = document->getUniqueObjectName("Scale");
    auto pcBody = dynamic_cast<PartDesign::Body*>(
        Part::BodyBase::findBodyOf(multiTransform)
    );
    if (!pcBody) {
        return;
    }

    const int creationTransactionId = isEnabledTransaction()
        ? pcBody->getDocument()->openTransaction(
              QT_TRANSLATE_NOOP("Command", "Scale")
          )
        : App::NullTransaction;

    auto* Feat = createTransformationExact<PartDesign::Scaled>(
        pcBody,
        "PartDesign::Scaled",
        newFeatName
    );
    if (!Feat) {
        abortCreationTransaction(document, creationTransactionId);
        return;
    }
    // Gui::Command::updateActive();
    FCMD_OBJ_CMD(Feat, "Factor = 2");
    FCMD_OBJ_CMD(Feat, "Occurrences = 2");

    const long featureId = Feat->getID();
    finishAdd(Feat);
    // show the new view when no error
    if (auto* liveFeature =
            freecad_cast<PartDesign::Scaled*>(
                document->getObjectByID(featureId)
            );
        liveFeature && !liveFeature->isError()
        && resolveMultiTransform() == multiTransform) {
        multiTransform->Visibility.setValue(true);
    }
}

void TaskMultiTransformParameters::finishAdd(
    PartDesign::Transformed* newFeature
)
{
    // Gui::Command::updateActive();
    // Gui::Command::copyVisual(newFeatName.c_str(), "ShapeColor",
    // getOriginals().front()->getNameInDocument().c_str());
    // Gui::Command::copyVisual(newFeatName.c_str(), "DisplayMode",
    // getOriginals().front()->getNameInDocument().c_str());

    if (!newFeature || !newFeature->isAttachedToDocument()
        || !newFeature->getNameInDocument()) {
        return;
    }
    auto* featureDocument = newFeature->getDocument();
    const long newFeatureId = newFeature->getID();
    const std::string newFeatureName = newFeature->getNameInDocument();
    const auto discardUnlinkedFeature = [&]() {
        auto* exactFeature = featureDocument->getObjectByID(newFeatureId);
        if (!exactFeature || !exactFeature->getNameInDocument()
            || newFeatureName != exactFeature->getNameInDocument()) {
            return;
        }
        auto* target = resolveMultiTransform();
        const auto linked = target
            ? target->Transformations.getValues()
            : std::vector<App::DocumentObject*> {};
        if (std::ranges::find(linked, exactFeature) == linked.end()) {
            featureDocument->removeObject(newFeatureName.c_str());
        }
    };

    auto* multiTransform = resolveMultiTransform();
    if (!isOwnedTransformation(multiTransform, newFeature)
        || !transformListMatches(multiTransform)) {
        discardUnlinkedFeature();
        rebuildTransformList();
        return;
    }

    auto transformFeatures = multiTransform->Transformations.getValues();
    std::size_t selectedPropertyIndex = 0;
    auto* selected = transformationForRow(
        multiTransform,
        ui->listTransformFeatures->currentRow(),
        transformFeatures,
        selectedPropertyIndex
    );

    setupTransaction();
    multiTransform = resolveMultiTransform();
    if (!multiTransform
        || multiTransform->getDocument()->getObjectByID(newFeatureId)
            != newFeature) {
        discardUnlinkedFeature();
        rebuildTransformList();
        return;
    }

    if (transformFeatures.empty()) {
        // Happens when first row (first transformation) is created
        // Hide all the originals now (hiding them in Command.cpp presents the user with an empty
        // screen!)
        hideBase();
    }

    // Insert after the exact live row the user selected.  If there is no
    // valid selected transformation, append rather than guessing an index.
    if (selected && selectedPropertyIndex < transformFeatures.size()) {
        transformFeatures.insert(
            transformFeatures.begin() + selectedPropertyIndex + 1,
            newFeature
        );
    }
    else {
        transformFeatures.push_back(newFeature);
    }
    multiTransform->Transformations.setValues(transformFeatures);

    recomputeMultiTransform(multiTransform);

    // Set state to hidden - only the MultiTransform should be visible
    if (multiTransform->getDocument()->getObjectByID(newFeatureId)
        == newFeature) {
        FCMD_OBJ_HIDE(newFeature);
    }
    editHint = false;
    rebuildTransformList(newFeatureId);
    onTransformEdit();
}

void TaskMultiTransformParameters::moveTransformFeature(const int increment)
{
    if (increment == 0 || !ensureTransformListSynchronized() || editHint) {
        return;
    }
    auto* multiTransform = resolveMultiTransform();
    auto transformFeatures = multiTransform->Transformations.getValues();
    const int row = ui->listTransformFeatures->currentRow();
    const int destinationRow = row + increment;
    if (row < 0 || destinationRow < 0
        || destinationRow >= ui->listTransformFeatures->count()) {
        updateOperationState();
        return;
    }

    std::size_t propertyIndex = 0;
    std::size_t destinationPropertyIndex = 0;
    auto* feature = transformationForRow(
        multiTransform,
        row,
        transformFeatures,
        propertyIndex
    );
    auto* destination = transformationForRow(
        multiTransform,
        destinationRow,
        transformFeatures,
        destinationPropertyIndex
    );
    if (!feature || !destination
        || !isOwnedTransformation(multiTransform, feature)
        || !isOwnedTransformation(multiTransform, destination)) {
        updateOperationState();
        return;
    }
    const long featureId = feature->getID();

    setupTransaction();
    multiTransform = resolveMultiTransform();
    if (!multiTransform || propertyIndex >= transformFeatures.size()
        || destinationPropertyIndex >= transformFeatures.size()
        || multiTransform->Transformations.getValues() != transformFeatures) {
        rebuildTransformList();
        return;
    }

    std::swap(
        transformFeatures[propertyIndex],
        transformFeatures[destinationPropertyIndex]
    );
    multiTransform->Transformations.setValues(transformFeatures);
    recomputeMultiTransform(multiTransform);
    rebuildTransformList(featureId);
}

void TaskMultiTransformParameters::onMoveUp()
{
    moveTransformFeature(-1);
}

void TaskMultiTransformParameters::onMoveDown()
{
    moveTransformFeature(+1);
}

void TaskMultiTransformParameters::onSubTaskButtonOK()
{
    closeSubTask();
}

void TaskMultiTransformParameters::onUpdateView(bool on)
{
    blockUpdate = !on;
    if (on) {
        recomputeMultiTransform(resolveMultiTransform());
    }
}

void TaskMultiTransformParameters::apply()
{
    auto* pcMultiTransform = resolveMultiTransform();
    if (!pcMultiTransform) {
        return;
    }
    std::vector<App::DocumentObject*> transformFeatures = pcMultiTransform->Transformations.getValues();
    std::stringstream str;
    str << Gui::Command::getObjectCmd(pcMultiTransform)
        << ".Transformations = [";
    for (auto it : transformFeatures) {
        if (isLiveTransformation(pcMultiTransform, it)) {
            str << Gui::Command::getObjectCmd(it) << ",";
        }
    }
    str << "]";
    Gui::Command::runCommand(Gui::Command::Doc, str.str().c_str());
}

TaskMultiTransformParameters::~TaskMultiTransformParameters()
{
    try {
        closeSubTask();
    }
    catch (const Py::Exception&) {
        Base::PyException exc;  // extract the Python error text
        exc.reportException();
    }
    catch (const Base::Exception& error) {
        error.reportException();
    }
    catch (const std::exception& error) {
        Base::Console().error(
            "Could not close the Multi-Transform child editor: %s\n",
            error.what()
        );
    }
    catch (...) {
        Base::Console().error(
            "Could not close the Multi-Transform child editor.\n"
        );
    }
}

//**************************************************************************
//**************************************************************************
// TaskDialog
//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

TaskDlgMultiTransformParameters::TaskDlgMultiTransformParameters(
    ViewProviderMultiTransform* MultiTransformView
)
    : TaskDlgTransformedParameters(MultiTransformView)
{
    parameter = new TaskMultiTransformParameters(MultiTransformView);
    parameter->setEnabledTransaction(false);

    Content.push_back(parameter);
    Content.push_back(preview);
}

void TaskDlgMultiTransformParameters::finalizeAcceptedFeature(
    App::DocumentObject* feature
)
{
    auto* multiTransform =
        freecad_cast<PartDesign::MultiTransform*>(feature);
    if (!multiTransform || !multiTransform->isAttachedToDocument()) {
        throw Base::RuntimeError(
            "The accepted Multi-Transform is no longer available"
        );
    }

    auto* timeline =
        App::DocumentTimeline::ensure(multiTransform->getDocument());
    const bool newRoot =
        timeline->isProvisionallyEnrolledByCurrentTransaction(
            multiTransform
        );

    multiTransform->synchronizeTimelineResources();
    std::vector<App::DocumentObject*> orderedNewObjects;
    std::vector<App::DocumentObject*> orderedStagedResources;
    for (auto* transformation :
         multiTransform->Transformations.getValues()) {
        if (!transformation
            || transformation->getDocument()
                != multiTransform->getDocument()) {
            throw Base::RuntimeError(
                "The accepted Multi-Transform contains an unavailable child"
            );
        }
        if (timeline->isProvisionallyEnrolledByCurrentTransaction(
                transformation
            )) {
            orderedNewObjects.push_back(transformation);
        }
        else if (newRoot) {
            orderedStagedResources.push_back(transformation);
        }
    }

    if (newRoot) {
        orderedNewObjects.push_back(multiTransform);
    }
    if (!orderedNewObjects.empty()) {
        timeline->finalizeProvisionalOperationBlock(
            multiTransform,
            orderedNewObjects,
            orderedStagedResources
        );
    }
}

#include "moc_TaskMultiTransformParameters.cpp"
