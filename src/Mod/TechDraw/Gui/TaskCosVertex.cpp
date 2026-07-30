/***************************************************************************
 *   Copyright (c) 2019 WandererFan <wandererfan@gmail.com>                *
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

# include <cmath>
# include <QStatusBar>

#include <Base/Console.h>
#include <Base/UnitsApi.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/Document.h>
#include <Gui/MainWindow.h>
#include <Gui/ViewProvider.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawProjGroup.h>
#include <Mod/TechDraw/App/DrawProjGroupItem.h>
#include <Mod/TechDraw/App/DrawUtil.h>
#include <Mod/TechDraw/App/DrawViewPart.h>
#include <Mod/TechDraw/App/Cosmetic.h>

#include "ui_TaskCosVertex.h"
#include "TaskCosVertex.h"
#include "MDIViewPage.h"
#include "QGIView.h"
#include "QGSPage.h"
#include "QGTracker.h"
#include "Rez.h"
#include "ViewProviderPage.h"


using namespace Gui;
using namespace TechDraw;
using namespace TechDrawGui;
using DU = DrawUtil;

TaskCosVertex::TaskCosVertex(TechDraw::DrawViewPart* baseFeat,
                               TechDraw::DrawPage* page) :
    ui(new Ui_TaskCosVertex),
    blockUpdate(false),
    m_tracker(nullptr),
    m_baseFeat(baseFeat),
    m_basePage(page),
    m_baseIdentity(baseFeat),
    m_pageIdentity(page),
    m_qgParent(nullptr),
    m_trackerMode(QGTracker::TrackerMode::None),
    m_saveContextPolicy(Qt::DefaultContextMenu),
    m_inProgressLock(false),
    m_btnOK(nullptr),
    m_btnCancel(nullptr),
    m_pbTrackerState(TrackerAction::PICK),
    m_savePoint(QPointF(0.0, 0.0))
{
    if (!baseFeat || !page
        || baseFeat->getDocument() != page->getDocument()) {
        throw Base::TypeError(
            "The cosmetic vertex requires a live view on one drawing page"
        );
    }

    ui->setupUi(this);

    Gui::Document* guiDocument =
        Gui::Application::Instance->getDocument(page->getDocument());
    Gui::ViewProvider* vp =
        guiDocument ? guiDocument->getViewProvider(page) : nullptr;
    m_vpp = dynamic_cast<ViewProviderPage*>(vp);
    if (!m_vpp || !m_vpp->getMDIViewPage()) {
        throw Base::RuntimeError(
            "The cosmetic vertex requires an open drawing page"
        );
    }

    setUiPrimary();

    connect(ui->pbTracker, &QPushButton::clicked,
            this, &TaskCosVertex::onTrackerClicked);

    m_trackerMode = QGTracker::TrackerMode::Point;
}

TaskCosVertex::~TaskCosVertex()
{
    removeTracker();
}

void TaskCosVertex::updateTask()
{
    //    blockUpdate = true;

    //    blockUpdate = false;
}

void TaskCosVertex::changeEvent(QEvent* event)
{
    if (event->type() == QEvent::LanguageChange) {
        ui->retranslateUi(this);
    }
}

void TaskCosVertex::setUiPrimary()
{
//    Base::Console().message("TCV::setUiPrimary()\n");
    setWindowTitle(QObject::tr("New Cosmetic Vertex"));

    if (m_baseFeat) {
        std::string baseName = m_baseFeat->getNameInDocument();
        ui->leBaseView->setText(QString::fromStdString(baseName));
    }
    ui->pbTracker->setText(tr("Pick Point"));
    ui->pbTracker->setEnabled(true);
    ui->dsbX->setEnabled(true);
    ui->dsbY->setEnabled(true);
    int decimals = Base::UnitsApi::getDecimals();
    ui->dsbX->setDecimals(decimals);
    ui->dsbY->setDecimals(decimals);
    ui->dsbX->setUnit(Base::Unit::Length);
    ui->dsbY->setUnit(Base::Unit::Length);
}

// set the ui x,y to apparent coords (ie invertY)
void TaskCosVertex::updateUi()
{
    double x = m_savePoint.x();
    double y = - m_savePoint.y();
    ui->dsbX->setValue(x);
    ui->dsbY->setValue(y);
}

//! create the cv as entered, addCosmeticVertex will invert it
void TaskCosVertex::addCosVertex(QPointF qPos)
{
//    Base::Vector3d pos = DU::invertY(DU::toVector3d(qPos));
//    int idx =
    (void) m_baseFeat->addCosmeticVertex(DU::toVector3d(qPos));
    m_baseFeat->requestPaint();
}


//********** Tracker routines *******************************************************************
void TaskCosVertex::onTrackerClicked(bool clicked)
{
    Q_UNUSED(clicked);
//    Base::Console().message("TCV::onTrackerClicked() m_pbTrackerState: %d\n",
//                            m_pbTrackerState);

    removeTracker();

    if (m_pbTrackerState == TrackerAction::CANCEL) {
        m_pbTrackerState = TrackerAction::PICK;
        ui->pbTracker->setText(tr("Pick Point"));
        enableTaskButtons(true);

        setEditCursor(Qt::ArrowCursor);
        return;
    }

    m_inProgressLock = true;
    m_saveContextPolicy = m_vpp->getMDIViewPage()->contextMenuPolicy();
    m_vpp->getMDIViewPage()->setContextMenuPolicy(Qt::PreventContextMenu);
    m_trackerMode = QGTracker::TrackerMode::Point;
    setEditCursor(Qt::CrossCursor);
    startTracker();

    QString msg = tr("Pick a point for cosmetic vertex");
    getMainWindow()->statusBar()->show();
    Gui::getMainWindow()->showMessage(msg, 3000);
    ui->pbTracker->setText(tr("Escape Picking"));
    ui->pbTracker->setEnabled(true);
    m_pbTrackerState = TrackerAction::CANCEL;
    enableTaskButtons(false);
}

void TaskCosVertex::startTracker()
{
//    Base::Console().message("TCV::startTracker()\n");
    if (m_trackerMode == QGTracker::TrackerMode::None) {
        return;
    }

    if (!m_tracker) {
        m_tracker = new QGTracker(m_vpp->getQGSPage(), m_trackerMode);
        std::string parentName = m_baseFeat->getNameInDocument();
        QGIView* parentView = m_vpp->getQGSPage()->getQGIVByName(parentName);
        m_tracker->setOwnerQView(parentView);
        QObject::connect(
            m_tracker, &QGTracker::drawingFinished,
            this, &TaskCosVertex::onTrackerFinished
        );
    }
    else {
        //this is too harsh. but need to avoid restarting process
        throw Base::RuntimeError("TechDrawNewLeader - tracker already active\n");
    }
    setEditCursor(Qt::CrossCursor);
    QString msg = tr("Left click to set a point");
    Gui::getMainWindow()->statusBar()->show();
    Gui::getMainWindow()->showMessage(msg, 3000);
}

void TaskCosVertex::onTrackerFinished(std::vector<QPointF> pts, QGIView* qgParent)
{
    (void)qgParent;
    if (pts.empty()) {
        Base::Console().error("TaskCosVertex - no points available\n");
        return;
    }

    QPointF dragEnd = pts.front();            //scene pos of mouse click

    double x = Rez::guiX(m_baseFeat->X.getValue());
    double y = Rez::guiX(m_baseFeat->Y.getValue());

    DrawViewPart* dvp = m_baseFeat;
    DrawProjGroupItem* dpgi = freecad_cast<DrawProjGroupItem*>(dvp);
    if (dpgi) {
        DrawProjGroup* dpg = dpgi->getPGroup();
        if (dpg) {
            x += Rez::guiX(dpg->X.getValue());
            y += Rez::guiX(dpg->Y.getValue());
        }
    }
    //x, y are scene pos of dvp/dpgi

    QPointF basePosScene(x, -y);                 //base position in scene coords
    QPointF scenePosCV = dragEnd - basePosScene;

    // Invert Y value so the math works.
    // scenePosCV is effectively a scaled (and rotated), inverted value
    // Base::Vector3d posToRotate = DU::invertY(DU::toVector3d(scenePosCV));

    // unscale and rotate the picked point
    Base::Vector3d posToRotate = CosmeticVertex::makeCanonicalPointInverted(m_baseFeat, DU::toVector3d(scenePosCV));
    // now put Y value back to display form
    scenePosCV = DU::toQPointF(posToRotate);

    m_savePoint = Rez::appX(scenePosCV);
    updateUi();

    m_tracker->sleep(true);
    m_inProgressLock = false;
    m_pbTrackerState = TrackerAction::PICK;
    ui->pbTracker->setText(tr("Pick Point"));
    ui->pbTracker->setEnabled(true);
    enableTaskButtons(true);
    setEditCursor(Qt::ArrowCursor);
    m_vpp->getMDIViewPage()->setContextMenuPolicy(m_saveContextPolicy);

}

void TaskCosVertex::removeTracker()
{
    if (m_tracker) {
        // QGraphicsItem destruction removes it from its scene.  Do not
        // dereference the page provider here: document-close cancellation
        // may already be tearing that provider down.
        delete m_tracker;
        m_tracker = nullptr;
    }
}

void TaskCosVertex::setEditCursor(QCursor cursor)
{
    if (m_baseFeat && m_vpp && m_vpp->getQGSPage()) {
        QGIView* qgivBase = m_vpp->getQGSPage()->findQViewForDocObj(m_baseFeat);
        if (qgivBase) {
            qgivBase->setCursor(cursor);
        }
    }
}

void TaskCosVertex::abandonEditSession()
{
    QString msg = tr("In progress edit abandoned. Start over.");
    getMainWindow()->statusBar()->show();
    Gui::getMainWindow()->showMessage(msg, 4000);

    ui->pbTracker->setEnabled(true);

    setEditCursor(Qt::ArrowCursor);
}

void TaskCosVertex::saveButtons(QPushButton* btnOK,
                             QPushButton* btnCancel)
{
    m_btnOK = btnOK;
    m_btnCancel = btnCancel;
}

void TaskCosVertex::enableTaskButtons(bool button)
{
    m_btnOK->setEnabled(button);
    m_btnCancel->setEnabled(button);
}

//******************************************************************************
bool TaskCosVertex::accept()
{
    auto* baseFeature = m_baseIdentity.resolve();
    auto* page = m_pageIdentity.resolve();
    if (!baseFeature || !page
        || baseFeature->getDocument() != page->getDocument()) {
        return false;
    }

    removeTracker();
    // whatever is in the ui for x,y is treated as an unscaled, unrotated, conventional Y position.
    // the position from the tracker is unscaled & unrotated before updating the ui
    double x = ui->dsbX->value().getValue();
    double y = ui->dsbY->value().getValue();
    QPointF uiPoint(x, y);

    try {
        TaskInternal::OwnedDocumentTransaction transaction(
            baseFeature->getDocument(),
            "Add Cosmetic Vertex"
        );
        m_baseFeat = baseFeature;
        addCosVertex(uiPoint);
        baseFeature->recomputeFeature();
        baseFeature->requestPaint();
        TaskInternal::updateExactDocument(baseFeature->getDocument());
        transaction.commit();
    }
    catch (const Base::Exception& error) {
        Base::Console().error(
            "Could not add cosmetic vertex: %s\n",
            error.what()
        );
        return false;
    }

    if (m_vpp && m_vpp->getMDIViewPage()) {
        m_vpp->getMDIViewPage()->setContextMenuPolicy(
            m_saveContextPolicy
        );
    }
    m_trackerMode = QGTracker::TrackerMode::None;
    TaskInternal::resetExactEdit(baseFeature->getDocument());

    return true;
}

bool TaskCosVertex::reject()
{
    removeTracker();
    m_trackerMode = QGTracker::TrackerMode::None;
    if (m_vpp && m_vpp->getMDIViewPage()) {
        m_vpp->getMDIViewPage()->setContextMenuPolicy(m_saveContextPolicy);
    }
    TaskInternal::resetExactEdit(m_pageIdentity.resolveDocument());
    return true;
}

/////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
TaskDlgCosVertex::TaskDlgCosVertex(TechDraw::DrawViewPart* baseFeat,
                                     TechDraw::DrawPage* page)
    : TaskDialog()
{
    widget  = new TaskCosVertex(baseFeat, page);
    taskbox = new Gui::TaskView::TaskBox(Gui::BitmapFactory().pixmap("actions/TechDraw_CosmeticVertex"),
                                             widget->windowTitle(), true, nullptr);
    taskbox->groupLayout()->addWidget(widget);
    Content.push_back(taskbox);
}

TaskDlgCosVertex::~TaskDlgCosVertex()
{
}

void TaskDlgCosVertex::update()
{
//    widget->updateTask();
}

void TaskDlgCosVertex::modifyStandardButtons(QDialogButtonBox* box)
{
    QPushButton* btnOK = box->button(QDialogButtonBox::Ok);
    QPushButton* btnCancel = box->button(QDialogButtonBox::Cancel);
    widget->saveButtons(btnOK, btnCancel);
}

//==== calls from the TaskView ===============================================================
void TaskDlgCosVertex::open()
{
}

void TaskDlgCosVertex::clicked(int)
{
}

bool TaskDlgCosVertex::accept()
{
    return widget->accept();
}

bool TaskDlgCosVertex::reject()
{
    return widget->reject();
}

#include <Mod/TechDraw/Gui/moc_TaskCosVertex.cpp>
