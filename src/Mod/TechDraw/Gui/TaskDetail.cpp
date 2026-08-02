/***************************************************************************
 *   Copyright (c) 2020 WandererFan <wandererfan@gmail.com>                *
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


#include <App/Document.h>
#include <Base/Console.h>
#include <Base/Interpreter.h>
#include <Base/UnitsApi.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/Document.h>
#include <Gui/Selection/Selection.h>
#include <Gui/ViewProvider.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawProjGroup.h>
#include <Mod/TechDraw/App/DrawProjGroupItem.h>
#include <Mod/TechDraw/App/DrawViewDetail.h>
#include <Mod/TechDraw/App/DrawViewPart.h>
#include <Mod/TechDraw/App/DrawUtil.h>

#include "ui_TaskDetail.h"
#include "TaskDetail.h"
#include "QGIGhostHighlight.h"
#include "QGSPage.h"
#include "Rez.h"
#include "ViewProviderPage.h"


using namespace TechDrawGui;
using namespace TechDraw;
using namespace Gui;

static constexpr int CREATEMODE(0);
static constexpr int EDITMODE(1);

//creation constructor
TaskDetail::TaskDetail(TechDraw::DrawViewPart* baseFeat):
    ui(new Ui_TaskDetail),
    blockUpdate(false),
    m_ghost(nullptr),
    m_detailFeat(nullptr),
    m_baseFeat(baseFeat),
    m_basePage(baseFeat ? baseFeat->findParentPage() : nullptr),
    m_qgParent(nullptr),
    m_inProgressLock(false),
    m_btnOK(nullptr),
    m_btnCancel(nullptr),
    m_saveAnchor(Base::Vector3d(0.0, 0.0, 0.0)),
    m_saveRadius(0.0),
    m_saved(false),
    m_doc(nullptr),
    m_mode(CREATEMODE),
    m_created(false)
{
    if (!m_baseFeat || !m_baseFeat->getDocument()) {
        throw Base::TypeError(
            "The detail task requires a live base view"
        );
    }

    m_basePage = m_baseFeat->findParentPage();
    //it is possible that the basePage could be unparented and have no corresponding Page
    if (!m_basePage) {
        throw Base::TypeError(
            "The detail base view is not on a drawing page"
        );
    }

    m_baseName = m_baseFeat->getNameInDocument();
    m_doc      = m_baseFeat->getDocument();
    m_pageName = m_basePage->getNameInDocument();
    m_baseIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawViewPart>(m_baseFeat);
    m_pageIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawPage>(m_basePage);

    ui->setupUi(this);

    Gui::Document* activeGui = Gui::Application::Instance->getDocument(m_doc);
    Gui::ViewProvider* vp = activeGui->getViewProvider(m_basePage);
    m_vpp = dynamic_cast<ViewProviderPage*>(vp);
    if (!m_vpp) {
        throw Base::TypeError(
            "The detail page has no compatible view provider"
        );
    }

    createDetail();
    setUiFromFeat();
    setWindowTitle(QObject::tr("New Detail View"));

    connect(ui->pbDragger, &QPushButton::clicked,
            this, &TaskDetail::onDraggerClicked);

    // the UI file uses keyboardTracking = false so that a recomputation
    // will only be triggered when the arrow keys of the spinboxes are used
    connect(ui->qsbX, qOverload<double>(&QuantitySpinBox::valueChanged),
            this, &TaskDetail::onXEdit);
    connect(ui->qsbY, qOverload<double>(&QuantitySpinBox::valueChanged),
            this, &TaskDetail::onYEdit);
    connect(ui->qsbRadius, qOverload<double>(&QuantitySpinBox::valueChanged),
            this, &TaskDetail::onRadiusEdit);
    connect(ui->cbScaleType, qOverload<int>(&QComboBox::currentIndexChanged),
            this, &TaskDetail::onScaleTypeEdit);
    connect(ui->qsbScale, qOverload<double>(&QuantitySpinBox::valueChanged),
            this, &TaskDetail::onScaleEdit);
    connect(ui->leReference, &QLineEdit::editingFinished,
            this, &TaskDetail::onReferenceEdit);

    m_ghost = new QGIGhostHighlight();
    m_vpp->getQGSPage()->addItem(m_ghost);
    m_ghost->hide();
    connect(m_ghost, &QGIGhostHighlight::positionChange,
            this, &TaskDetail::onHighlightMoved);
}

//edit constructor
TaskDetail::TaskDetail(TechDraw::DrawViewDetail* detailFeat):
    ui(new Ui_TaskDetail),
    blockUpdate(false),
    m_ghost(nullptr),
    m_detailFeat(detailFeat),
    m_baseFeat(nullptr),
    m_basePage(nullptr),
    m_qgParent(nullptr),
    m_inProgressLock(false),
    m_btnOK(nullptr),
    m_btnCancel(nullptr),
    m_saveAnchor(Base::Vector3d(0.0, 0.0, 0.0)),
    m_saveRadius(0.0),
    m_saved(false),
    m_doc(nullptr),
    m_mode(EDITMODE),
    m_created(false)
{
    if (!m_detailFeat)  {
        //should be caught in CMD caller
        throw Base::TypeError(
            "The detail task requires a live detail view"
        );
    }

    m_doc = m_detailFeat->getDocument();
    m_detailName = m_detailFeat->getNameInDocument();
    m_detailIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawViewDetail>(
            m_detailFeat
        );

    m_basePage = m_detailFeat->findParentPage();
    if (m_basePage) {
        m_pageName = m_basePage->getNameInDocument();
    }
    else {
        throw Base::TypeError(
            "The detail view is not on a drawing page"
        );
    }

    App::DocumentObject* baseObj = m_detailFeat->BaseView.getValue();
    m_baseFeat = dynamic_cast<TechDraw::DrawViewPart*>(baseObj);
    if (!m_baseFeat) {
        throw Base::TypeError(
            "The detail view has no live base view"
        );
    }
    m_baseName = m_baseFeat->getNameInDocument();
    m_baseIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawViewPart>(m_baseFeat);
    m_pageIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawPage>(m_basePage);
    // repaint baseObj here to make highlight inactive.
    m_baseFeat->requestPaint();

    ui->setupUi(this);

    Gui::Document* activeGui = Gui::Application::Instance->getDocument(m_basePage->getDocument());
    Gui::ViewProvider* vp = activeGui->getViewProvider(m_basePage);
    m_vpp = dynamic_cast<ViewProviderPage*>(vp);
    if (!m_vpp) {
        throw Base::TypeError(
            "The detail page has no compatible view provider"
        );
    }

    saveDetailState();
    setUiFromFeat();
    setWindowTitle(QObject::tr("Edit Detail View"));

    connect(ui->pbDragger, &QPushButton::clicked,
            this, &TaskDetail::onDraggerClicked);

    // the UI file uses keyboardTracking = false so that a recomputation
    // will only be triggered when the arrow keys of the spinboxes are used
    connect(ui->qsbX, qOverload<double>(&QuantitySpinBox::valueChanged),
            this, &TaskDetail::onXEdit);
    connect(ui->qsbY, qOverload<double>(&QuantitySpinBox::valueChanged),
            this, &TaskDetail::onYEdit);
    connect(ui->qsbRadius, qOverload<double>(&QuantitySpinBox::valueChanged),
            this, &TaskDetail::onRadiusEdit);
    connect(ui->cbScaleType, qOverload<int>(&QComboBox::currentIndexChanged),
            this, &TaskDetail::onScaleTypeEdit);
    connect(ui->qsbScale, qOverload<double>(&QuantitySpinBox::valueChanged),
            this, &TaskDetail::onScaleEdit);
    connect(ui->leReference, &QLineEdit::editingFinished,
            this, &TaskDetail::onReferenceEdit);

    m_ghost = new QGIGhostHighlight();
    m_vpp->getQGSPage()->addItem(m_ghost);
    m_ghost->hide();
    connect(m_ghost, &QGIGhostHighlight::positionChange,
            this, &TaskDetail::onHighlightMoved);
}

void TaskDetail::updateTask()
{
//    blockUpdate = true;

//    blockUpdate = false;
}

void TaskDetail::changeEvent(QEvent *e)
{
    if (e->type() == QEvent::LanguageChange) {
        ui->retranslateUi(this);
    }
}

//save the start conditions
void TaskDetail::saveDetailState()
{
    TechDraw::DrawViewDetail* dvd = getDetailFeat();
    m_saveAnchor = dvd->AnchorPoint.getValue();
    m_saveRadius  = dvd->Radius.getValue();
    m_saved = true;
}

void TaskDetail::restoreDetailState()
{
    TechDraw::DrawViewDetail* dvd = getDetailFeat();
    dvd->AnchorPoint.setValue(m_saveAnchor);
    dvd->Radius.setValue(m_saveRadius);
}

//***** ui stuff ***************************************************************

void TaskDetail::setUiFromFeat()
{
    if (m_baseFeat) {
        std::string baseName = getBaseFeat()->getNameInDocument();
        ui->leBaseView->setText(QString::fromStdString(baseName));
    }

    Base::Vector3d anchor;

    TechDraw::DrawViewDetail* detailFeat = getDetailFeat();
    QString detailDisplay = QString::fromUtf8(detailFeat->getNameInDocument()) +
                            QStringLiteral(" / ") +
                            QString::fromUtf8(detailFeat->Label.getValue());
    ui->leDetailView->setText(detailDisplay);
    anchor = detailFeat->AnchorPoint.getValue();
    double radius = detailFeat->Radius.getValue();
    long ScaleType = detailFeat->ScaleType.getValue();
    double scale = detailFeat->Scale.getValue();
    QString ref = QString::fromUtf8(detailFeat->Reference.getValue());

    ui->pbDragger->setText(tr("Drag Highlight"));
    ui->pbDragger->setEnabled(true);
    int decimals = Base::UnitsApi::getDecimals();
    ui->qsbX->setUnit(Base::Unit::Length);
    ui->qsbX->setDecimals(decimals);
    ui->qsbX->setValue(anchor.x);
    ui->qsbY->setUnit(Base::Unit::Length);
    ui->qsbY->setDecimals(decimals);
    ui->qsbY->setValue(anchor.y);
    ui->qsbRadius->setDecimals(decimals);
    ui->qsbRadius->setUnit(Base::Unit::Length);
    ui->qsbRadius->setValue(radius);
    ui->qsbScale->setDecimals(decimals);
    ui->cbScaleType->setCurrentIndex(ScaleType);
    if (ui->cbScaleType->currentIndex() == 2) { // only if custom scale
        ui->qsbScale->setEnabled(true);
    }
    else {
        ui->qsbScale->setEnabled(false);
    }
    ui->qsbScale->setValue(scale);
    ui->leReference->setText(ref);
}

//update ui point fields after tracker finishes
void TaskDetail::updateUi(QPointF pos)
{
    ui->qsbX->blockSignals(true);
    ui->qsbY->blockSignals(true);

    ui->qsbX->setValue(pos.x());
    ui->qsbY->setValue(pos.y());

    ui->qsbX->blockSignals(false);
    ui->qsbY->blockSignals(false);
}

void TaskDetail::enableInputFields(bool isEnabled)
{
    ui->qsbX->setEnabled(isEnabled);
    ui->qsbY->setEnabled(isEnabled);
    if (ui->cbScaleType->currentIndex() == 2) {    // only if custom scale
        ui->qsbScale->setEnabled(isEnabled);
    }
    ui->qsbRadius->setEnabled(isEnabled);
    ui->leReference->setEnabled(isEnabled);
}

void TaskDetail::onXEdit()
{
    updateDetail();
}

void TaskDetail::onYEdit()
{
    updateDetail();
}

void TaskDetail::onRadiusEdit()
{
    updateDetail();
}

void TaskDetail::onScaleTypeEdit()
{
    TechDraw::DrawViewDetail* detailFeat = getDetailFeat();

    detailFeat->ScaleType.setValue(ui->cbScaleType->currentIndex());
    if (ui->cbScaleType->currentIndex() == 0) {
         // page scale
         ui->qsbScale->setEnabled(false);
         // set the page scale if there is a valid page
         if (m_basePage) {
             // set the page scale
             detailFeat->Scale.setValue(m_basePage->Scale.getValue());
             ui->qsbScale->setValue(m_basePage->Scale.getValue());
         }
         // finally update the view
         updateDetail();
    }
    else if (ui->cbScaleType->currentIndex() == 1) {
        // automatic scale (if view is too large to fit into page, it will be scaled down)
        ui->qsbScale->setEnabled(false);
        // updating the feature will trigger the rescaling
        updateDetail();
    }
    else if (ui->cbScaleType->currentIndex() == 2) {
        // custom scale
        ui->qsbScale->setEnabled(true);
    }
}

void TaskDetail::onScaleEdit()
{
    updateDetail();
}

void TaskDetail::onReferenceEdit()
{
    updateDetail();
}

void TaskDetail::onDraggerClicked(bool clicked)
{
    Q_UNUSED(clicked);
    ui->pbDragger->setEnabled(false);
    enableInputFields(false);
    editByHighlight();
}

void TaskDetail::editByHighlight()
{
    if (!m_ghost) {
        Base::Console().error("TaskDetail::editByHighlight - no ghost object\n");
        return;
    }

    double scale = getBaseFeat()->getScale();
    m_vpp->getQGSPage()->clearSelection();
    m_ghost->setSelected(true);
    m_ghost->setRadius(ui->qsbRadius->rawValue() * scale);
    m_ghost->setPos(getAnchorScene());
    m_ghost->draw();
    m_ghost->show();
}

//dragEnd is in scene coords.
void TaskDetail::onHighlightMoved(QPointF dragEnd)
{
    ui->pbDragger->setEnabled(true);

    double radius = m_detailFeat->Radius.getValue();
    double scale = getBaseFeat()->getScale();
    double x = Rez::guiX(getBaseFeat()->X.getValue());
    double y = Rez::guiX(getBaseFeat()->Y.getValue());

    DrawViewPart* dvp = getBaseFeat();
    auto* dpgi = freecad_cast<DrawProjGroupItem*>(dvp);
    DrawProjGroup* dpg{nullptr};
    if (dpgi && DrawView::isProjGroupItem(dpgi)) {
        dpg = dpgi->getPGroup();
    }

    if (dpg) {
        x += Rez::guiX(dpg->X.getValue());
        y += Rez::guiX(dpg->Y.getValue());
    }

    QPointF basePosScene(x, -y);                 //base position in scene coords
    QPointF anchorDisplace = dragEnd - basePosScene;
    QPointF newAnchorPosScene = Rez::appX(anchorDisplace / scale);


    Base::Vector3d newAnchorPosPage = DrawUtil::toVector3d(newAnchorPosScene);
    newAnchorPosPage = DrawUtil::invertY(newAnchorPosPage);
    Base::Vector3d snappedPos = dvp->snapHighlightToVertex(newAnchorPosPage, radius);

    updateUi(DrawUtil::toQPointF(snappedPos));
    updateDetail();
    enableInputFields(true);

    m_ghost->setSelected(false);
    m_ghost->hide();
}

void TaskDetail::saveButtons(QPushButton* btnOK,
                             QPushButton* btnCancel)
{
    m_btnOK = btnOK;
    m_btnCancel = btnCancel;
}

void TaskDetail::enableTaskButtons(bool button)
{
    m_btnOK->setEnabled(button);
    m_btnCancel->setEnabled(button);
}

//***** Feature create & edit stuff *******************************************
void TaskDetail::createDetail()
{
    if (!m_doc
        || m_doc->getBookedTransactionID() == App::NullTransaction) {
        throw Base::RuntimeError(
            "The detail task has no owning transaction"
        );
    }

    const std::string objectName{"Detail"};
    m_detailName = m_doc->getUniqueObjectName(objectName.c_str());
    const std::string documentName = m_doc->getName();

    const QString detailFactory =
        QStringLiteral(
            "App.getDocument('%1').addObject("
            "'TechDraw::DrawViewDetail', '%2')"
        )
            .arg(
                QString::fromStdString(
                    Base::InterpreterSingleton::strToPython(documentName)
                ),
                QString::fromStdString(m_detailName)
            );
    auto* dvd = dynamic_cast<TechDraw::DrawViewDetail*>(
        Gui::Command::runDocumentObjectCommand(
            Command::Doc,
            *m_doc,
            detailFactory.toUtf8(),
            TechDraw::DrawViewDetail::getClassTypeId()
        )
    );
    if (!dvd) {
        throw Base::TypeError(
            "TaskDetail - detail factory returned an incompatible object"
        );
    }
    m_detailName = dvd->getNameInDocument();

    Gui::Command::doCommand(
        Command::Doc,
        "App.getDocument('%s').getObject('%s').translateLabel("
        "'DrawViewDetail', 'Detail', '%s')",
        documentName.c_str(),
        m_detailName.c_str(),
        m_detailName.c_str()
    );

    m_detailFeat = dvd;
    m_detailIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawViewDetail>(dvd);
    dvd->Source.setValues(getBaseFeat()->Source.getValues());

    auto* page = m_pageIdentity.resolve();
    if (!page) {
        throw Base::RuntimeError(
            "The detail task lost its drawing page"
        );
    }
    Gui::Command::doCommand(
        Command::Doc,
        "App.getDocument('%s').getObject('%s').BaseView = "
        "App.getDocument('%s').getObject('%s')",
        documentName.c_str(),
        m_detailName.c_str(),
        documentName.c_str(),
        m_baseName.c_str()
    );
    for (const char* property : {"Direction", "XDirection", "Scale"}) {
        Gui::Command::doCommand(
            Command::Doc,
            "setattr(App.getDocument('%s').getObject('%s'), '%s', "
            "getattr(App.getDocument('%s').getObject('%s'), '%s'))",
            documentName.c_str(),
            m_detailName.c_str(),
            property,
            documentName.c_str(),
            m_baseName.c_str(),
            property
        );
    }
    Gui::Command::doCommand(
        Command::Doc,
        "App.getDocument('%s').getObject('%s').addView("
        "App.getDocument('%s').getObject('%s'))",
        documentName.c_str(),
        m_pageName.c_str(),
        documentName.c_str(),
        m_detailName.c_str()
    );

    Gui::Command::updateDocument(m_doc);

    getBaseFeat()->requestPaint();
    m_created = true;
}

void TaskDetail::updateDetail()
{
    TechDraw::DrawViewDetail* detailFeat = getDetailFeat();
    try {
        double x = ui->qsbX->rawValue();
        double y = ui->qsbY->rawValue();
        Base::Vector3d temp(x, y, 0.0);

        detailFeat->AnchorPoint.setValue(temp);     // point2d

        double scale = ui->qsbScale->rawValue();
        detailFeat->Scale.setValue(scale);
        double radius = ui->qsbRadius->rawValue();
        detailFeat->Radius.setValue(radius);
        QString qRef = ui->leReference->text();
        std::string ref = qRef.toStdString();
        detailFeat->Reference.setValue(ref);

        Gui::Command::updateDocument(detailFeat->getDocument());
    }
    catch (...) {
        //this is probably due to appl closing while dialog is still open
        Base::Console().error("Task detail - detail feature update failed.\n");
    }

    detailFeat->recomputeFeature();
}

//***** Getters ****************************************************************

//get the current Anchor highlight position in scene coords
QPointF TaskDetail::getAnchorScene()
{
    DrawViewPart* dvp = getBaseFeat();
    auto* dpgi = freecad_cast<DrawProjGroupItem*>(dvp);
    DrawViewDetail* dvd = getDetailFeat();
    Base::Vector3d anchorPos = dvd->AnchorPoint.getValue();
    anchorPos.y = -anchorPos.y;
    Base::Vector3d basePos;
    double scale = 1;

    double x = dvp->X.getValue();
    double y = dvp->Y.getValue();
    scale = dvp->getScale();

    DrawProjGroup* dpg{nullptr};
    if (dpgi && DrawProjGroup::isProjGroupItem(dpgi)) {
        dpg = dpgi->getPGroup();
    }

    if (dpg) {
        // part of a projection group
        x = dpg->X.getValue();
        x += dpgi->X.getValue();
        y = dpg->Y.getValue();
        y += dpgi->Y.getValue();
        scale = dpgi->getScale();
    }

    basePos = Base::Vector3d (x, -y, 0.0);

    Base::Vector3d xyScene = Rez::guiX(basePos);
    Base::Vector3d anchorOffsetScene = Rez::guiX(anchorPos) * scale;
    Base::Vector3d netPos = xyScene + anchorOffsetScene;
    return {netPos.x, netPos.y};
}

// protects against stale pointers
DrawViewPart* TaskDetail::getBaseFeat()
{
    if (auto* base = m_baseIdentity.resolve()) {
        return base;
    }

    std::string msg = "TaskDetail - base feature " +
                        m_baseName +
                        " not found \n";
    throw Base::TypeError(msg);
    return nullptr;
}

// protects against stale pointers
DrawViewDetail* TaskDetail::getDetailFeat()
{
    if (auto* detail = m_detailIdentity.resolve()) {
        return detail;
    }

    std::string msg = "TaskDetail - detail feature " +
                        m_detailName +
                        " not found \n";
    throw Base::TypeError(msg);
    return nullptr;
}

//******************************************************************************

bool TaskDetail::accept()
{
    auto* page = m_pageIdentity.resolve();
    if (!page || !m_ghost) {
        return false;
    }

    m_ghost->hide();
    auto* detail = getDetailFeat();
    detail->recomputeFeature();
    if (detail->isError()) {
        return false;
    }

    TaskInternal::resetExactEdit(page->getDocument());

    return true;
}

bool TaskDetail::reject()
{
    auto* page = m_pageIdentity.resolve();
    if (!page) {
        return false;
    }

    if (m_ghost) {
        m_ghost->hide();
    }
    // TaskView rolls both creation and live edit changes back through the
    // exact transaction after the task widgets have been removed.
    TaskInternal::resetExactEdit(page->getDocument());

    return false;
}

/////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
TaskDlgDetail::TaskDlgDetail(TechDraw::DrawViewPart* baseFeat)
    : TaskDialog()
{
    widget  = new TaskDetail(baseFeat);
    taskbox = new Gui::TaskView::TaskBox(Gui::BitmapFactory().pixmap("actions/TechDraw_DetailView"),
                                             widget->windowTitle(), true, nullptr);
    taskbox->groupLayout()->addWidget(widget);
    Content.push_back(taskbox);
}

TaskDlgDetail::TaskDlgDetail(TechDraw::DrawViewDetail* detailFeat)
    : TaskDialog()
{
    widget  = new TaskDetail(detailFeat);
    taskbox = new Gui::TaskView::TaskBox(Gui::BitmapFactory().pixmap("actions/TechDraw_DetailView"),
                                             widget->windowTitle(), true, nullptr);
    taskbox->groupLayout()->addWidget(widget);
    Content.push_back(taskbox);
}

TaskDlgDetail::~TaskDlgDetail()
{
}

void TaskDlgDetail::update()
{
//    widget->updateTask();
}

void TaskDlgDetail::modifyStandardButtons(QDialogButtonBox* box)
{
    QPushButton* btnOK = box->button(QDialogButtonBox::Ok);
    QPushButton* btnCancel = box->button(QDialogButtonBox::Cancel);
    widget->saveButtons(btnOK, btnCancel);
}

std::string TaskDlgDetail::getDetailName() const
{
    DrawViewDetail* detailObj = widget->getDetailFeat();
    if (!detailObj) {
        return {"not found"};
    }

    return detailObj->getNameInDocument();
}


//==== calls from the TaskView ===============================================================
void TaskDlgDetail::open()
{
}

void TaskDlgDetail::clicked(int)
{
}

bool TaskDlgDetail::accept()
{
    return widget->accept();
}

bool TaskDlgDetail::reject()
{
    widget->reject();
    return true;
}

#include <Mod/TechDraw/Gui/moc_TaskDetail.cpp>
