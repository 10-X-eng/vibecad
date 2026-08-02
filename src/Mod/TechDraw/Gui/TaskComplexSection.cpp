/***************************************************************************
 *   Copyright (c) 2022 WandererFan <wandererfan@gmail.com>                *
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

#include <QMessageBox>
#include <gp_Pnt.hxx>

#include <App/Document.h>
#include <App/Link.h>
#include <Base/Console.h>
#include <Base/Converter.h>
#include <Base/Interpreter.h>
#include <Base/Tools.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/Control.h>
#include <Gui/MainWindow.h>
#include <Gui/Selection/Selection.h>
#include <Gui/WaitCursor.h>

#include "Widgets/CompassWidget.h"
#include "Widgets/VectorEditWidget.h"
#include <Mod/TechDraw/App/DrawComplexSection.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawUtil.h>
#include <Mod/TechDraw/App/DrawViewPart.h>
#include <Mod/TechDraw/App/Preferences.h>

#include "DrawGuiUtil.h"
#include "TaskComplexSection.h"
#include "ui_TaskComplexSection.h"


using namespace Gui;
using namespace TechDraw;
using namespace TechDrawGui;
using DU = DrawUtil;

namespace
{
gp_Ax2 makeWorldSectionCS(
    const Base::Vector3d& viewDirection,
    const Base::Vector3d& preferredXDirection
)
{
    const Base::Vector3d sectionNormal = viewDirection * -1.0;
    gp_Ax2 coordinateSystem(
        gp_Pnt(0.0, 0.0, 0.0),
        Base::convertTo<gp_Dir>(sectionNormal)
    );
    if (!preferredXDirection.IsNull()) {
        try {
            coordinateSystem = gp_Ax2(
                gp_Pnt(0.0, 0.0, 0.0),
                Base::convertTo<gp_Dir>(sectionNormal),
                Base::convertTo<gp_Dir>(preferredXDirection)
            );
        }
        catch (...) {
            // A preferred direction parallel to the section normal cannot
            // define the X axis.  The gp_Ax2 constructor above supplies a
            // deterministic perpendicular axis in that case.
        }
    }
    return coordinateSystem;
}
}

//ctor for creation
TaskComplexSection::TaskComplexSection(TechDraw::DrawPage* page, TechDraw::DrawViewPart* baseView,
                                       std::vector<App::DocumentObject*> shapes,
                                       std::vector<App::DocumentObject*> xShapes,
                                       App::DocumentObject* profileObject,
                                       std::vector<std::string> profileSubs) :
    ui(new Ui_TaskComplexSection),
    m_page(page),
    m_baseView(baseView),
    m_section(nullptr),
    m_shapes(shapes),
    m_xShapes(xShapes),
    m_profileObject(profileObject),
    m_profileSubs(profileSubs),
    m_dirName("Aligned"),
    m_createMode(true),
    m_applyDeferred(0),
    m_angle(0.0),
    m_directionIsSet(false),
    m_modelIsDirty(false),
    m_scaleEdited(false)
{
    m_sectionName = std::string();
    if (!m_page || !m_profileObject
        || m_page->getDocument()->getBookedTransactionID()
            == App::NullTransaction
        || (m_baseView
            && m_baseView->getDocument()
                != m_page->getDocument())
        || (!m_baseView && m_shapes.empty() && m_xShapes.empty())) {
        throw Base::RuntimeError(
            "The complex section requires a live page, profile, source "
            "geometry, and owning transaction"
        );
    }
    m_doc = m_page->getDocument();
    m_documentIdentity = TaskInternal::DocumentIdentity(m_doc);
    m_pageIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawPage>(m_page);
    m_baseIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawViewPart>(m_baseView);
    m_profileIdentity =
        TaskInternal::ObjectIdentity<App::DocumentObject>(
            m_profileObject
        );
    ui->setupUi(this);

    saveSectionState();
    captureSources();
    setUiPrimary();

    m_applyDeferred = 0;//setting the direction widgets causes an increment of the deferred count,
                        //so we reset the counter and the message.
}

//ctor for edit
TaskComplexSection::TaskComplexSection(TechDraw::DrawComplexSection* complexSection) :
    ui(new Ui_TaskComplexSection),
    m_page(nullptr),
    m_baseView(nullptr),
    m_section(complexSection),
    m_profileObject(nullptr),
    m_dirName("Aligned"),
    m_sectionName(),
    m_createMode(false),
    m_applyDeferred(0),
    m_angle(0.0),
    m_directionIsSet(true),
    m_modelIsDirty(false),
    m_scaleEdited(false)
{
    if (!m_section) {
        throw Base::TypeError(
            "The complex-section editor requires a live section"
        );
    }
    m_doc = m_section->getDocument();
    m_page = m_section->findParentPage();
    m_profileObject =
        m_section->CuttingToolWireObject.getValue();
    if (!m_page || !m_profileObject
        || m_page->getDocument() != m_doc
        || m_doc->getBookedTransactionID()
            == App::NullTransaction) {
        throw Base::RuntimeError(
            "The complex section has no live page, profile, or owning "
            "transaction"
        );
    }
    m_sectionName = m_section->getNameInDocument();

    m_baseView = dynamic_cast<TechDraw::DrawViewPart*>(m_section->BaseView.getValue());
    if (m_baseView && m_baseView->getDocument() != m_doc) {
        throw Base::RuntimeError(
            "The complex section base view belongs to another document"
        );
    }

    m_shapes = m_section->Source.getValues();
    m_xShapes = m_section->XSource.getValues();
    m_documentIdentity = TaskInternal::DocumentIdentity(m_doc);
    m_pageIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawPage>(m_page);
    m_baseIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawViewPart>(m_baseView);
    m_sectionIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawComplexSection>(
            m_section
        );
    m_profileIdentity =
        TaskInternal::ObjectIdentity<App::DocumentObject>(
            m_profileObject
        );
    captureSources();

    ui->setupUi(this);

    saveSectionState();
    setUiEdit();

    m_applyDeferred = 0;//setting the direction widgets causes an increment of the deferred count,
                        //so we reset the counter and the message.
    ui->lPendingUpdates->setText(QString());
}

void TaskComplexSection::captureSources()
{
    m_shapeIdentities.clear();
    m_shapeIdentities.reserve(m_shapes.size());
    for (auto* shape : m_shapes) {
        if (shape) {
            m_shapeIdentities.emplace_back(shape);
        }
    }
    m_xShapeIdentities.clear();
    m_xShapeIdentities.reserve(m_xShapes.size());
    for (auto* shape : m_xShapes) {
        if (shape) {
            m_xShapeIdentities.emplace_back(shape);
        }
    }
}

bool TaskComplexSection::resolveTargets()
{
    auto* document = m_documentIdentity.resolve();
    auto* page = m_pageIdentity.resolve();
    auto* profile = m_profileIdentity.resolve();
    if (!document || !page || !profile
        || page->getDocument() != document) {
        return false;
    }

    auto* base = m_baseIdentity.resolve();
    if (m_baseView && !base) {
        return false;
    }
    auto* section = m_sectionIdentity.resolve();
    if (m_section && !section) {
        return false;
    }

    m_doc = document;
    m_page = page;
    m_profileObject = profile;
    m_baseView = base;
    m_section = section;
    return resolveSources();
}

bool TaskComplexSection::resolveSources()
{
    if (m_baseView) {
        m_shapes = m_baseView->Source.getValues();
        m_xShapes = m_baseView->XSource.getValues();
        return !m_shapes.empty() || !m_xShapes.empty();
    }

    std::vector<App::DocumentObject*> shapes;
    shapes.reserve(m_shapeIdentities.size());
    for (const auto& identity : m_shapeIdentities) {
        auto* shape = identity.resolve();
        if (!shape) {
            return false;
        }
        shapes.push_back(shape);
    }
    std::vector<App::DocumentObject*> xShapes;
    xShapes.reserve(m_xShapeIdentities.size());
    for (const auto& identity : m_xShapeIdentities) {
        auto* shape = identity.resolve();
        if (!shape) {
            return false;
        }
        xShapes.push_back(shape);
    }
    if (shapes.empty() && xShapes.empty()) {
        return false;
    }
    m_shapes = std::move(shapes);
    m_xShapes = std::move(xShapes);
    return true;
}

void TaskComplexSection::setUiPrimary()
{
    setWindowTitle(QObject::tr("New Complex Section"));
    if (m_baseView) {
        ui->sbScale->setValue(m_baseView->getScale());
        ui->cmbScaleType->setCurrentIndex(m_baseView->getScaleType());
    }
    else {
        ui->sbScale->setValue(Preferences::scale());
        ui->cmbScaleType->setCurrentIndex(Preferences::scaleType());
    }
    ui->cmbStrategy->setCurrentIndex(0);

    setUiCommon();

    if (m_baseView) {
        ui->leBaseView->setText(QString::fromStdString(m_baseView->getNameInDocument()));
        //if there is a baseView, we don't know the sectionNormal yet and have to wait until
        //one is picked in the dialog
        Base::Vector3d defaultNormal(-1.0, 0.0, 0.0);
        m_saveNormal = defaultNormal;
        m_saveXDir = Base::Vector3d(0.0, 1.0, 0.0);
        ui->leBaseView->setText(QString::fromStdString(m_baseView->getNameInDocument()));
        m_compass->setDialAngle(0.0);
        m_viewDirectionWidget->setValueNoNotify(Base::Vector3d(1.0, 0.0, 0.0));
    }
    else {
        //if there is no baseView, we use the 3d view to determine the SectionNormal
        //and XDirection.
        std::pair<Base::Vector3d, Base::Vector3d> dirs = DrawGuiUtil::get3DDirAndRot();
        m_saveNormal = dirs.first;
        m_saveXDir = dirs.second;
        m_viewDirectionWidget->setValue(m_saveNormal * -1);//this will propagate to m_compass
    }

    //don't allow updates until a direction is picked
    ui->pbUpdateNow->setEnabled(false);
    ui->cbLiveUpdate->setEnabled(false);
    ui->lPendingUpdates->setText(tr("No direction set"));
}

void TaskComplexSection::setUiEdit()
{
    setWindowTitle(QObject::tr("Edit Complex Section"));

    if (m_baseView) {
        ui->leBaseView->setText(QString::fromStdString(m_baseView->getNameInDocument()));
    }
    ui->cmbStrategy->setCurrentIndex(m_section->ProjectionStrategy.getValue());
    ui->leSymbol->setText(QString::fromStdString(m_section->SectionSymbol.getValue()));
    ui->sbScale->setValue(m_section->Scale.getValue());
    ui->cmbScaleType->setCurrentIndex(m_section->getScaleType());

    setUiCommon();

    Base::Vector3d sectionNormalVec = m_section->SectionNormal.getValue();
    if (m_baseView) {
        ui->leBaseView->setText(QString::fromStdString(m_baseView->getNameInDocument()));
        Base::Vector3d projectedViewDirection = m_baseView->projectPoint(sectionNormalVec, false);
        double viewAngle = atan2(-projectedViewDirection.y, -projectedViewDirection.x);
        m_compass->setDialAngle(Base::toDegrees(viewAngle));
        m_viewDirectionWidget->setValueNoNotify(projectedViewDirection * -1);
    }
    else {
        //no local angle makes sense if there is no baseView?
        m_viewDirectionWidget->setValue(sectionNormalVec * -1.0);
    }
}

void TaskComplexSection::setUiCommon()
{
    ui->leSectionObjects->setText(sourcesToString());
    ui->leProfileObject->setText(QString::fromStdString(m_profileObject->getNameInDocument())
                                 + QStringLiteral(" / ")
                                 + QString::fromStdString(m_profileObject->Label.getValue()));

    m_compass = new CompassWidget(this);
    auto layout = ui->compassLayout;
    layout->addWidget(m_compass);

    m_viewDirectionWidget = new VectorEditWidget(this);
    m_viewDirectionWidget->setLabel(QObject::tr("Current View Direction"));
    m_viewDirectionWidget->setToolTip(QObject::tr("The view direction in BaseView coordinates"));
    auto editLayout = ui->viewDirectionLayout;
    editLayout->addWidget(m_viewDirectionWidget);


    connect(m_compass, &CompassWidget::angleChanged, this, &TaskComplexSection::slotChangeAngle);

    connect(ui->pbUp, &QPushButton::clicked, this, &TaskComplexSection::onUpClicked);
    connect(ui->pbDown, &QPushButton::clicked, this, &TaskComplexSection::onDownClicked);
    connect(ui->pbRight, &QPushButton::clicked, this, &TaskComplexSection::onRightClicked);
    connect(ui->pbLeft, &QPushButton::clicked, this, &TaskComplexSection::onLeftClicked);

    connect(ui->pbUpdateNow, &QPushButton::clicked, this, &TaskComplexSection::updateNowClicked);
    connect(ui->cbLiveUpdate, &QCheckBox::clicked, this, &TaskComplexSection::liveUpdateClicked);

    connect(ui->pbSectionObjects, &QPushButton::clicked, this,
            &TaskComplexSection::onSectionObjectsUseSelectionClicked);
    connect(ui->pbProfileObject, &QPushButton::clicked, this,
            &TaskComplexSection::onProfileObjectsUseSelectionClicked);

    connect(m_viewDirectionWidget, &VectorEditWidget::valueChanged, this,
            &TaskComplexSection::slotViewDirectionChanged);
}

//save the start conditions
void TaskComplexSection::saveSectionState()
{
    if (m_section) {
        m_saveSymbol = m_section->SectionSymbol.getValue();
        m_saveScale = m_section->getScale();
        m_saveScaleType = m_section->getScaleType();
        m_saveNormal = m_section->SectionNormal.getValue();
        m_saveDirection = m_section->Direction.getValue();
        m_saveXDir = m_section->XDirection.getValue();
        m_saveOrigin = m_section->SectionOrigin.getValue();
        m_saveDirName = m_section->SectionDirection.getValueAsString();
        m_saved = true;
    }
    if (m_baseView) {
        m_shapes = m_baseView->Source.getValues();
        m_xShapes = m_baseView->XSource.getValues();
    }
}

//restore the start conditions
void TaskComplexSection::restoreSectionState()
{
    if (!m_section){
        return;
    }

    m_section->SectionSymbol.setValue(m_saveSymbol);
    m_section->Scale.setValue(m_saveScale);
    m_section->ScaleType.setValue(m_saveScaleType);
    m_section->SectionNormal.setValue(m_saveNormal);
    m_section->Direction.setValue(m_saveDirection);
    m_section->XDirection.setValue(m_saveXDir);
    m_section->SectionOrigin.setValue(m_saveOrigin);
    m_section->SectionDirection.setValue(m_saveDirName.c_str());
}

void TaskComplexSection::onSectionObjectsUseSelectionClicked()
{
    std::vector<Gui::SelectionObject> selection = Gui::Selection().getSelectionEx();
    std::vector<App::DocumentObject*> newSelection;
    std::vector<App::DocumentObject*> newXSelection;
    for (auto& sel : selection) {
        auto* object = sel.getObject();
        if (!object || object == m_page || object == m_section
            || object == m_profileObject) {
            continue;
        }
        if (object->isDerivedFrom<App::LinkElement>()
            || object->isDerivedFrom<App::LinkGroup>()
            || object->isDerivedFrom<App::Link>()) {
            newXSelection.push_back(object);
        }
        else {
            newSelection.push_back(object);
        }
    }
    if (newSelection.empty() && newXSelection.empty()) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("No Section Objects"),
            tr("Select at least one model object or link.")
        );
        return;
    }
    // Choosing explicit section objects switches from base-view inheritance
    // to direct source mode; otherwise this button would visibly accept a
    // selection which the section calculation then ignores.
    m_baseView = nullptr;
    m_baseIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawViewPart>();
    m_shapes = newSelection;
    m_xShapes = newXSelection;
    captureSources();
    ui->leSectionObjects->setText(sourcesToString());
}

//the VectorEditWidget reports a change in direction
void TaskComplexSection::slotViewDirectionChanged(Base::Vector3d newDirection)
{
    Base::Vector3d projectedViewDirection = newDirection;
    if (m_baseView) {
        projectedViewDirection = m_baseView->projectPoint(newDirection, false);
    }
    projectedViewDirection.Normalize();
    double viewAngle = atan2(projectedViewDirection.y, projectedViewDirection.x);
    m_compass->setDialAngle(Base::toDegrees(viewAngle));
    checkAll(false);
    applyAligned();
}

//the CompassWidget reports the view direction.  This is the reverse of the
//SectionNormal
void TaskComplexSection::slotChangeAngle(double newAngle)
{
    double angleRadians = Base::toRadians(newAngle);
    double unitX = cos(angleRadians);
    double unitY = sin(angleRadians);
    Base::Vector3d localUnit(unitX, unitY, 0.0);
    m_viewDirectionWidget->setValueNoNotify(localUnit);
    checkAll(false);
    applyAligned();
}

void TaskComplexSection::onUpClicked()
{
    checkAll(false);
    m_compass->setToNorth();
    m_viewDirectionWidget->setValueNoNotify(Base::Vector3d(0.0, 1.0, 0.0));
    applyAligned();
}

void TaskComplexSection::onDownClicked()
{
    checkAll(false);
    m_compass->setToSouth();
    m_viewDirectionWidget->setValueNoNotify(Base::Vector3d(0.0, -1.0, 0.0));
    applyAligned();
}

void TaskComplexSection::onLeftClicked()
{
    checkAll(false);
    m_compass->setToWest();
    m_viewDirectionWidget->setValueNoNotify(Base::Vector3d(-1.0, 0.0, 0.0));
    applyAligned();
}

void TaskComplexSection::onRightClicked()
{
    checkAll(false);
    m_compass->setToEast();
    m_viewDirectionWidget->setValueNoNotify(Base::Vector3d(1.0, 0.0, 0.0));
    applyAligned();
}

void TaskComplexSection::onIdentifierChanged()
{
    checkAll(false);
    apply();
}

void TaskComplexSection::onScaleChanged()
{
    m_scaleEdited = true;
    checkAll(false);
    apply();
}

void TaskComplexSection::onProfileObjectsUseSelectionClicked()
{
    std::vector<Gui::SelectionObject> selection = Gui::Selection().getSelectionEx();
    //check for single selection and ability to make profile from selected object
    if (selection.size() != 1 || !selection.front().getObject()) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("No Section Profile"),
            tr("Select exactly one profile object.")
        );
        return;
    }
    m_profileObject = selection.front().getObject();
    m_profileSubs = selection.front().getSubNames();
    m_profileIdentity =
        TaskInternal::ObjectIdentity<App::DocumentObject>(
            m_profileObject
        );
    ui->leProfileObject->setText(
        QString::fromStdString(m_profileObject->getNameInDocument())
        + QStringLiteral(" / ")
        + QString::fromStdString(m_profileObject->Label.getValue()));
}
void TaskComplexSection::scaleTypeChanged(int index)
{
    if (index == 0) {
        // Page Scale Type
        ui->sbScale->setEnabled(false);
        if (auto* page = m_pageIdentity.resolve()) {
            ui->sbScale->setValue(page->Scale.getValue());
            ui->sbScale->setEnabled(false);
        }
    }
    else if (index == 1) {
        // Automatic Scale Type
        ui->sbScale->setEnabled(false);
        if (m_section) {
            ui->sbScale->setValue(m_section->autoScale());
        }
    }
    else if (index == 2) {
        // Custom Scale Type
        ui->sbScale->setEnabled(true);
        if (m_section) {
            ui->sbScale->setValue(m_section->Scale.getValue());
            ui->sbScale->setEnabled(true);
        }
    }
    else {
        return;
    }
}

void TaskComplexSection::checkAll(bool check)
{
    ui->pbUp->setChecked(check);
    ui->pbDown->setChecked(check);
    ui->pbRight->setChecked(check);
    ui->pbLeft->setChecked(check);
}

void TaskComplexSection::enableAll(bool enable)
{
    ui->leSymbol->setEnabled(enable);
    ui->sbScale->setEnabled(enable);
    ui->cmbScaleType->setEnabled(enable);
    QString qScaleType = ui->cmbScaleType->currentText();
    //Allow or prevent scale changing initially
    if (qScaleType == QStringLiteral("Custom")) {
        ui->sbScale->setEnabled(true);
    }
    else {
        ui->sbScale->setEnabled(false);
    }
}

void TaskComplexSection::liveUpdateClicked() { apply(true); }

void TaskComplexSection::updateNowClicked() { apply(true); }

QString TaskComplexSection::sourcesToString()
{
    if (!resolveSources()) {
        return tr("Source geometry is unavailable");
    }
    QString result;
    QString separator(QStringLiteral(", "));
    QString currentSeparator;
    if (m_baseView) {
        for (auto& obj : m_baseView->Source.getValues()) {
            if (!obj) {
                continue;
            }
            result += currentSeparator + QString::fromStdString(obj->getNameInDocument())
                + QStringLiteral(" / ") + QString::fromStdString(obj->Label.getValue());
            currentSeparator = separator;
        }
        for (auto& obj : m_baseView->XSource.getValues()) {
            if (!obj) {
                continue;
            }
            result += currentSeparator + QString::fromStdString(obj->getNameInDocument())
                + QStringLiteral(" / ") + QString::fromStdString(obj->Label.getValue());
            currentSeparator = separator;
        }
    }
    else {
        for (auto& obj : m_shapes) {
            if (!obj) {
                continue;
            }
            result += currentSeparator + QString::fromStdString(obj->getNameInDocument())
                + QStringLiteral(" / ") + QString::fromStdString(obj->Label.getValue());
            currentSeparator = separator;
        }
        for (auto& obj : m_xShapes) {
            if (!obj) {
                continue;
            }
            result += currentSeparator + QString::fromStdString(obj->getNameInDocument())
                + QStringLiteral(" / ") + QString::fromStdString(obj->Label.getValue());
            currentSeparator = separator;
        }
    }
    return result;
}

//******************************************************************************
bool TaskComplexSection::apply(bool forceUpdate)
{
    if (!ui->cbLiveUpdate->isChecked() && !forceUpdate) {
        //nothing to do
        m_applyDeferred++;
        QString msgLiteral =
            QString::fromUtf8(QT_TRANSLATE_NOOP("TaskComplexSection", " updates pending"));
        QString msgNumber = QString::number(m_applyDeferred);
        ui->lPendingUpdates->setText(msgNumber + msgLiteral);
        return false;
    }

    if (!resolveTargets()) {
        Base::Console().error(
            "The complex section target or source geometry is no longer "
            "available.\n"
        );
        return false;
    }
    Base::Vector3d localUnit = m_viewDirectionWidget->value();
    if (localUnit.IsNull()) {
        Base::Console().error(
            "The complex section direction cannot be zero.\n"
        );
        return false;
    }
    localUnit.Normalize();
    if (m_baseView) {
        if (!DrawComplexSection::canBuild(m_baseView->localVectorToCS(localUnit * -1.0),
                                          m_profileObject)) {
            Base::Console().error(
                "Cannot build complex section with this profile and direction (1)\n");
            return false;
        }
    }
    else {
        const gp_Ax2 sectionCS =
            makeWorldSectionCS(localUnit, m_saveXDir);
        if (!DrawComplexSection::canBuild(sectionCS, m_profileObject)) {
            Base::Console().error(
                "Cannot build complex section with this profile and direction (2)\n");
            return false;
        }
    }

    Gui::WaitCursor wc;
    m_modelIsDirty = true;

    if (!m_section) {
        createComplexSection();
        if (!isSectionValid()) {
            return false;
        }
    }

    if (isSectionValid()) {
        updateComplexSection();
    }
    else {
        failNoObject();
        return false;
    }

    m_section->recomputeFeature();
    if (isBaseValid()) {
        m_baseView->requestPaint();
    }
    if (!m_section->checkSectionCS()) {
            QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Possible coordinate system error"),
                                               QObject::tr("Check SectionNormal, Direction and/or XDirection."));
    }


    enableAll(true);
    checkAll(false);

    wc.restoreCursor();
    m_applyDeferred = 0;
    ui->lPendingUpdates->setText(QString());
    return true;
}

void TaskComplexSection::applyAligned()
{
    m_dirName = "Aligned";
    enableAll(true);
    m_directionIsSet = true;
    ui->pbUpdateNow->setEnabled(true);
    ui->cbLiveUpdate->setEnabled(true);
    apply();
}

//*******************************************************************

//pointer to created view is not returned, but stored in m_section
void TaskComplexSection::createComplexSection()
{
    if (!m_section) {
        auto* document = m_documentIdentity.resolve();
        auto* page = m_pageIdentity.resolve();
        if (!document || !page || !resolveSources()) {
            throw Base::RuntimeError(
                "The complex section target is no longer available"
            );
        }
        const std::string objectName{QT_TR_NOOP("ComplexSection")};
        m_sectionName =
            document->getUniqueObjectName(objectName.c_str());
        const std::string documentName =
            Base::InterpreterSingleton::strToPython(
                document->getName()
            );
        const QString sectionFactory =
            QStringLiteral(
                "App.getDocument('%1').addObject"
                "('TechDraw::DrawComplexSection', '%2')"
            )
                .arg(
                    QString::fromStdString(documentName),
                    QString::fromStdString(m_sectionName)
                );
        m_section = dynamic_cast<TechDraw::DrawComplexSection*>(
            Gui::Command::runDocumentObjectCommand(
                Command::Doc,
                *document,
                sectionFactory.toUtf8(),
                TechDraw::DrawComplexSection::getClassTypeId()
            )
        );
        if (!m_section) {
            throw Base::RuntimeError(
                "The complex section object could not be created"
            );
        }
        m_sectionName = m_section->getNameInDocument();
        m_sectionIdentity =
            TaskInternal::ObjectIdentity<
                TechDraw::DrawComplexSection
            >(m_section);
        const std::string sectionCommand =
            Gui::Command::getObjectCmd(m_section);
        const std::string pageCommand =
            Gui::Command::getObjectCmd(page);

        // section labels (Section A-A) are not unique, and are not the same as the object name (SectionView)
        // we pluck the generated suffix from the object name and append it to "Section" to generate
        // unique Labels
        QString qTemp = ui->leSymbol->text();
        std::string temp = qTemp.toStdString();
        const std::string symbol =
            Base::InterpreterSingleton::strToPython(temp.c_str());
        const std::string labelText = makeSectionLabel(qTemp);
        const std::string label =
            Base::InterpreterSingleton::strToPython(
                labelText.c_str()
            );
        Command::doCommand(
            Command::Doc,
            "%s.SectionSymbol = '%s'",
            sectionCommand.c_str(),
            symbol.c_str()
        );
        Command::doCommand(
            Command::Doc,
            "%s.Label = '%s'",
            sectionCommand.c_str(),
            label.c_str()
        );
        Command::doCommand(
            Command::Doc,
            "%s.translateLabel('DrawViewSection', 'Section', '%s')",
            sectionCommand.c_str(),
            label.c_str()
        );
        Command::doCommand(
            Command::Doc,
            "%s.addView(%s)",
            pageCommand.c_str(),
            sectionCommand.c_str()
        );
        Command::doCommand(
            Command::Doc,
            "%s.Scale = %0.7f",
            sectionCommand.c_str(),
            ui->sbScale->value()
        );

        int scaleType = ui->cmbScaleType->currentIndex();
        Command::doCommand(
            Command::Doc,
            "%s.ScaleType = %d",
            sectionCommand.c_str(),
            scaleType
        );
        int projectionStrategy = ui->cmbStrategy->currentIndex();
        Command::doCommand(
            Command::Doc,
            "%s.ProjectionStrategy = %d",
            sectionCommand.c_str(),
            projectionStrategy
        );
        Command::doCommand(
            Command::Doc,
            "%s.SectionOrigin = FreeCAD.Vector(0.0, 0.0, 0.0)",
            sectionCommand.c_str()
        );
        Command::doCommand(
            Command::Doc,
            "%s.SectionDirection = 'Aligned'",
            sectionCommand.c_str()
        );

        Base::Vector3d localUnit = m_viewDirectionWidget->value();
        localUnit.Normalize();
        if (m_baseView) {
            const std::string baseCommand =
                Gui::Command::getObjectCmd(m_baseView);
            Command::doCommand(
                Command::Doc,
                "%s.BaseView = %s",
                sectionCommand.c_str(),
                baseCommand.c_str()
            );
            m_section->setCSFromBase(localUnit * -1.0);
            m_section->Source.setValues(m_baseView->Source.getValues());
            m_section->XSource.setValues(m_baseView->XSource.getValues());
        }
        else {
            const gp_Ax2 sectionCS =
                makeWorldSectionCS(localUnit, m_saveXDir);
            const Base::Vector3d sectionNormal =
                Base::convertTo<Base::Vector3d>(
                    sectionCS.Direction()
                );
            m_section->SectionNormal.setValue(sectionNormal);
            m_section->Direction.setValue(sectionNormal);
            m_section->XDirection.setValue(
                Base::convertTo<Base::Vector3d>(
                    sectionCS.XDirection()
                )
            );
            m_section->Source.setValues(m_shapes);
            m_section->XSource.setValues(m_xShapes);
        }
        m_section->CuttingToolWireObject.setValue(m_profileObject);
        m_section->SectionDirection.setValue("Aligned");

        //auto orientation of view relative to base view
        double viewDirectionAngle = m_compass->positiveValue();
        double rotation = requiredRotation(viewDirectionAngle);
        Command::doCommand(
            Command::Doc,
            "%s.Rotation = %.6f",
            sectionCommand.c_str(),
            rotation
        );

    }
}

void TaskComplexSection::updateComplexSection()
{
    if (!isSectionValid()) {
        failNoObject();
        return;
    }

    if (m_section) {
        const std::string sectionCommand =
            Gui::Command::getObjectCmd(m_section);
        QString qTemp = ui->leSymbol->text();
        std::string temp = qTemp.toStdString();
        const std::string symbol =
            Base::InterpreterSingleton::strToPython(temp.c_str());
        const std::string labelText = makeSectionLabel(qTemp);
        const std::string label =
            Base::InterpreterSingleton::strToPython(
                labelText.c_str()
            );
        Command::doCommand(
            Command::Doc,
            "%s.SectionSymbol = '%s'",
            sectionCommand.c_str(),
            symbol.c_str()
        );
        Command::doCommand(
            Command::Doc,
            "%s.Label = '%s'",
            sectionCommand.c_str(),
            label.c_str()
        );
        Command::doCommand(
            Command::Doc,
            "%s.translateLabel('DrawViewSection', 'Section', '%s')",
            sectionCommand.c_str(),
            label.c_str()
        );
        Command::doCommand(
            Command::Doc,
            "%s.Scale = %0.7f",
            sectionCommand.c_str(),
            ui->sbScale->value()
        );

        int scaleType = ui->cmbScaleType->currentIndex();
        Command::doCommand(
            Command::Doc,
            "%s.ScaleType = %d",
            sectionCommand.c_str(),
            scaleType
        );
        int projectionStrategy = ui->cmbStrategy->currentIndex();
        Command::doCommand(
            Command::Doc,
            "%s.ProjectionStrategy = %d",
            sectionCommand.c_str(),
            projectionStrategy
        );
        Command::doCommand(
            Command::Doc,
            "%s.SectionDirection = 'Aligned'",
            sectionCommand.c_str()
        );

        m_section->CuttingToolWireObject.setValue(m_profileObject);
        m_section->SectionDirection.setValue("Aligned");
        Base::Vector3d localUnit = m_viewDirectionWidget->value();
        localUnit.Normalize();
        if (m_baseView) {
            const std::string baseCommand =
                Gui::Command::getObjectCmd(m_baseView);
            Command::doCommand(
                Command::Doc,
                "%s.BaseView = %s",
                sectionCommand.c_str(),
                baseCommand.c_str()
            );
            m_section->setCSFromBase(localUnit * -1.0);
            m_section->Source.setValues(m_baseView->Source.getValues());
            m_section->XSource.setValues(m_baseView->XSource.getValues());
        }
        else {
            Command::doCommand(
                Command::Doc,
                "%s.BaseView = None",
                sectionCommand.c_str()
            );
            const gp_Ax2 sectionCS =
                makeWorldSectionCS(localUnit, m_saveXDir);
            const Base::Vector3d sectionNormal =
                Base::convertTo<Base::Vector3d>(
                    sectionCS.Direction()
                );
            m_section->SectionNormal.setValue(sectionNormal);
            m_section->Direction.setValue(sectionNormal);
            m_section->XDirection.setValue(
                Base::convertTo<Base::Vector3d>(
                    sectionCS.XDirection()
                )
            );
            m_section->Source.setValues(m_shapes);
            m_section->XSource.setValues(m_xShapes);
        }

        //auto orientation of view relative to base view
        double viewDirectionAngle = m_compass->positiveValue();
        double rotation = requiredRotation(viewDirectionAngle);

        Command::doCommand(
            Command::Doc,
            "%s.Rotation = %.6f",
            sectionCommand.c_str(),
            rotation
        );
    }
}

std::string TaskComplexSection::makeSectionLabel(const QString& symbol)
{
    const std::string objectName{QT_TR_NOOP("ComplexSection")};
    const std::string uniqueSuffix =
        m_sectionName.rfind(objectName, 0) == 0
        ? m_sectionName.substr(objectName.length())
        : m_sectionName;
    std::string uniqueLabel = "Section" + uniqueSuffix;
    std::string temp = symbol.toStdString();
    return ( uniqueLabel + " " + temp + " - " + temp );
}

void TaskComplexSection::failNoObject()
{
    QString qsectionName = QString::fromStdString(m_sectionName);
    QString qbaseName = m_baseIdentity.name().empty()
        ? tr("source geometry")
        : QString::fromStdString(m_baseIdentity.name());
    QString msg = tr("Can not continue. Object * %1 or %2 not found.").arg(qsectionName, qbaseName);
    QMessageBox::critical(Gui::getMainWindow(), QObject::tr("Operation Failed"), msg);
    Gui::Control().closeDialog(m_documentIdentity.resolve());
}

bool TaskComplexSection::isBaseValid()
{
    auto* base = m_baseIdentity.resolve();
    if (!base) {
        return false;
    }
    m_baseView = base;
    return true;
}

bool TaskComplexSection::isSectionValid()
{
    auto* section = m_sectionIdentity.resolve();
    if (!section) {
        return false;
    }
    m_section = section;
    return true;
}

//get required rotation from input angle in [0, 360]
//NOTE: shared code with simple section - reuse opportunity
double TaskComplexSection::requiredRotation(double inputAngleDeg)
{
    constexpr double PiOver4Degrees{90};
    constexpr double PiOver2Degrees{180};
    double rotation = inputAngleDeg - PiOver4Degrees;
    if (rotation == PiOver2Degrees) {
        //if the view direction is 90/270, then the section is drawn properly and no
        //rotation is needed.  90.0 becomes 0.0, but 270.0 needs special handling.
        rotation = 0.0;
    }
    return rotation;
}

//******************************************************************************
bool TaskComplexSection::accept()
{
    if (!apply(true) || !isSectionValid() || m_section->isError()) {
        return false;
    }
    TaskInternal::updateExactDocument(m_section->getDocument());
    TaskInternal::resetExactEdit(m_section->getDocument());
    return true;
}

bool TaskComplexSection::reject()
{
    if (isBaseValid()) {
        m_baseView->requestPaint();
    }
    // TaskView owns the exact creation/edit transaction. Cancel rolls every
    // provisional object and live parameter change back as one operation.
    TaskInternal::resetExactEdit(m_documentIdentity.resolve());

    return true;
}

void TaskComplexSection::changeEvent(QEvent* event)
{
    if (event->type() == QEvent::LanguageChange) {
        ui->retranslateUi(this);
    }
}

/////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
TaskDlgComplexSection::TaskDlgComplexSection(TechDraw::DrawPage* page,
                                             TechDraw::DrawViewPart* baseView,
                                             std::vector<App::DocumentObject*> shapes,
                                             std::vector<App::DocumentObject*> xShapes,
                                             App::DocumentObject* profileObject,
                                             std::vector<std::string> profileSubs)
{
    widget = new TaskComplexSection(page, baseView, shapes, xShapes, profileObject, profileSubs);
    taskbox =
        new Gui::TaskView::TaskBox(Gui::BitmapFactory().pixmap("actions/TechDraw_ComplexSection"),
                                   widget->windowTitle(), true, nullptr);
    taskbox->groupLayout()->addWidget(widget);
    Content.push_back(taskbox);
    setAutoCloseOnTransactionChange(true);
}

TaskDlgComplexSection::TaskDlgComplexSection(TechDraw::DrawComplexSection* complexSection)
    : widget(new TaskComplexSection(complexSection))
{
    taskbox =
        new Gui::TaskView::TaskBox(Gui::BitmapFactory().pixmap("actions/TechDraw_ComplexSection"),
                                   widget->windowTitle(), true, nullptr);
    taskbox->groupLayout()->addWidget(widget);
    Content.push_back(taskbox);
    setAutoCloseOnTransactionChange(true);
}

void TaskDlgComplexSection::update()
{
    //    widget->updateTask();
}

//==== calls from the TaskView ===============================================================
void TaskDlgComplexSection::open() {}

bool TaskDlgComplexSection::accept()
{
    return widget->accept();
}

bool TaskDlgComplexSection::reject()
{
    return widget->reject();
}

//NOLINTNEXTLINE
#include <Mod/TechDraw/Gui/moc_TaskComplexSection.cpp>
