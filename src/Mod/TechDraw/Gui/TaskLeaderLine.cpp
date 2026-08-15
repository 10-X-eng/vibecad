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

# include <QStatusBar>

#include <QCoreApplication>
#include <QMessageBox>

#include <App/Document.h>
#include <Base/Console.h>
#include <Base/Tools.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Document.h>
#include <Gui/MainWindow.h>
#include <Gui/ViewProvider.h>
#include <Mod/TechDraw/App/DrawLeaderLine.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawView.h>
#include <Mod/TechDraw/App/DrawUtil.h>

#include "TaskLeaderLine.h"
#include "TaskDocumentGuard.h"
#include "ui_TaskLeaderLine.h"
#include "DrawGuiUtil.h"
#include "LeaderLineBuilder.h"
#include "MDIViewPage.h"
#include "QGILeaderLine.h"
#include "QGIView.h"
#include "QGSPage.h"
#include "QGTracker.h"
#include "Rez.h"
#include "ViewProviderLeader.h"
#include "ViewProviderPage.h"


using namespace Gui;
using namespace TechDraw;
using namespace TechDrawGui;
using DU = DrawUtil;
using DGU = DrawGuiUtil;

constexpr int MessageDisplayTime{3000};

TaskLeaderLine::TaskLeaderLine(TechDrawGui::ViewProviderLeader* leadVP) :
    ui(new Ui_TaskLeaderLine),
    m_tracker(nullptr),
    m_lineVP(leadVP),
    m_baseFeat(nullptr),
    m_basePage(nullptr),
    m_lineFeat(m_lineVP ? m_lineVP->getFeature() : nullptr),
    m_qgParent(nullptr),
    m_createMode(false),
    m_trackerMode(QGTracker::TrackerMode::None),
    m_saveContextPolicy(Qt::DefaultContextMenu),
    m_inProgressLock(false),
    m_qgLeader(nullptr),
    m_btnOK(nullptr),
    m_btnCancel(nullptr),
    m_pbTrackerState(TrackerAction::EDIT),
    m_saveX(0.0),
    m_saveY(0.0)
{
    if (!m_lineVP || !m_lineFeat) {
        throw Base::TypeError(
            "The leader editor requires a live leader"
        );
    }
    m_basePage = m_lineFeat->findParentPage();
    App::DocumentObject* obj = m_lineFeat->LeaderParent.getValue();
    if (obj) {
        if (obj->isDerivedFrom<TechDraw::DrawView>() )  {
            m_baseFeat = static_cast<TechDraw::DrawView*>(m_lineFeat->LeaderParent.getValue());
        }
    }

    if (!m_baseFeat || !m_basePage
        || m_baseFeat->getDocument()
            != m_lineFeat->getDocument()
        || m_basePage->getDocument()
            != m_lineFeat->getDocument()
        || m_baseFeat->findParentPage() != m_basePage
        || m_lineFeat->getDocument()->getBookedTransactionID()
            == App::NullTransaction) {
        throw Base::RuntimeError(
            "The leader editor requires a leader attached to a live page "
            "and its owning transaction"
        );
    }
    Gui::Document* activeGui =
        Gui::Application::Instance->getDocument(
            m_basePage->getDocument()
        );
    Gui::ViewProvider* vp =
        activeGui ? activeGui->getViewProvider(m_basePage) : nullptr;
    m_vpp = dynamic_cast<ViewProviderPage*>(vp);
    if (!m_vpp || !m_vpp->getQGSPage()) {
        throw Base::RuntimeError(
            "The leader editor could not find the drawing page"
        );
    }

    m_qgParent = nullptr;
    if (m_baseFeat) {
        m_qgParent = m_vpp->getQGSPage()->findQViewForDocObj(m_baseFeat);
    }

    ui->setupUi(this);

    setUiEdit();

    m_attachPoint = Rez::guiX(Base::Vector3d(m_lineFeat->X.getValue(),
                                            -m_lineFeat->Y.getValue(),
                                             0.0));

    connect(ui->pbTracker, &QPushButton::clicked,
            this, &TaskLeaderLine::onTrackerClicked);
    connect(ui->pbCancelEdit, &QPushButton::clicked,
            this, &TaskLeaderLine::onCancelEditClicked);
    ui->pbCancelEdit->setEnabled(false);

    saveState();

    m_trackerMode = QGTracker::TrackerMode::Line;
    if (m_vpp->getMDIViewPage()) {
        m_saveContextPolicy = m_vpp->getMDIViewPage()->contextMenuPolicy();
    }
}

//ctor for creation
TaskLeaderLine::TaskLeaderLine(TechDraw::DrawView* baseFeat,
                               TechDraw::DrawPage* page) :
    ui(new Ui_TaskLeaderLine),
    m_tracker(nullptr),
    m_lineVP(nullptr),
    m_baseFeat(baseFeat),
    m_basePage(page),
    m_lineFeat(nullptr),
    m_qgParent(nullptr),
    m_createMode(true),
    m_trackerMode(QGTracker::TrackerMode::None),
    m_saveContextPolicy(Qt::DefaultContextMenu),
    m_inProgressLock(false),
    m_qgLeader(nullptr),
    m_btnOK(nullptr),
    m_btnCancel(nullptr),
    m_pbTrackerState(TrackerAction::PICK),
    m_saveX(0.0),
    m_saveY(0.0)
{
    if (!m_baseFeat || !m_basePage
        || m_baseFeat->getDocument()
            != m_basePage->getDocument()
        || m_baseFeat->findParentPage() != m_basePage) {
        throw Base::RuntimeError(
            "A new leader requires a base view on the selected page"
        );
    }
    Gui::Document* activeGui =
        Gui::Application::Instance->getDocument(
            m_basePage->getDocument()
        );
    Gui::ViewProvider* vp =
        activeGui ? activeGui->getViewProvider(m_basePage) : nullptr;
    m_vpp = dynamic_cast<ViewProviderPage*>(vp);
    if (!m_vpp || !m_vpp->getQGSPage()) {
        throw Base::RuntimeError(
            "The leader task could not find the drawing page"
        );
    }

    if (m_baseFeat) {
        m_qgParent = m_vpp->getQGSPage()->findQViewForDocObj(baseFeat);
    }

    ui->setupUi(this);

    setUiPrimary();

    connect(ui->pbTracker, &QPushButton::clicked,
            this, &TaskLeaderLine::onTrackerClicked);
    connect(ui->pbCancelEdit, &QPushButton::clicked,
            this, &TaskLeaderLine::onCancelEditClicked);
    ui->pbCancelEdit->setEnabled(false);

    m_trackerMode = QGTracker::TrackerMode::Line;
    if (m_vpp->getMDIViewPage()) {
        m_saveContextPolicy = m_vpp->getMDIViewPage()->contextMenuPolicy();
    }
}

TaskLeaderLine::~TaskLeaderLine()
{
    removeTracker();
}

void TaskLeaderLine::saveState()
{
    if (m_lineFeat) {
        m_savePoints = m_lineFeat->WayPoints.getValues();
        m_saveX = m_lineFeat->X.getValue();
        m_saveY = m_lineFeat->Y.getValue();
    }
}

void TaskLeaderLine::restoreState()
{
    if (m_lineFeat) {
        m_lineFeat->WayPoints.setValues(m_savePoints);
        m_lineFeat->X.setValue(m_saveX);
        m_lineFeat->Y.setValue(m_saveY);
    }
}

void TaskLeaderLine::updateTask()
{
//    blockUpdate = true;

//    blockUpdate = false;
}

void TaskLeaderLine::changeEvent(QEvent *event)
{
    if (event->type() == QEvent::LanguageChange) {
        ui->retranslateUi(this);
    }
}

void TaskLeaderLine::setUiPrimary()
{
    enableVPUi(true);
    setWindowTitle(QObject::tr("New Leader Line"));

    if (m_baseFeat) {
        std::string baseName = m_baseFeat->getNameInDocument();
        ui->tbBaseView->setText(QString::fromStdString(baseName));
    }

    ui->pbTracker->setText(tr("Pick Points"));
    if (m_vpp->getMDIViewPage()) {
        ui->pbTracker->setEnabled(true);
        ui->pbCancelEdit->setEnabled(true);
    } else {
        ui->pbTracker->setEnabled(false);
        ui->pbCancelEdit->setEnabled(false);
    }

    const DrawingLeaderDefaults defaults = drawingLeaderDefaults();

    DrawGuiUtil::loadArrowBox(ui->cboxStartSym);
    ui->cboxStartSym->setCurrentIndex(defaults.style.startSymbol);

    DrawGuiUtil::loadArrowBox(ui->cboxEndSym);
    ui->cboxEndSym->setCurrentIndex(defaults.style.endSymbol);

    ui->dsbWeight->setUnit(Base::Unit::Length);
    ui->dsbWeight->setMinimum(0);
    ui->dsbWeight->setValue(defaults.style.lineWidthMm);

    ui->cboxStyle->setCurrentIndex(defaults.style.lineStyle);
    ui->cpLineColor->setColor(defaults.style.lineColor.asValue<QColor>());
}

//switch widgets related to ViewProvider on/off
//there is no ViewProvider until some time after feature is created.
void TaskLeaderLine::enableVPUi(bool enable)
{
    ui->cpLineColor->setEnabled(enable);
    ui->dsbWeight->setEnabled(enable);
    ui->cboxStyle->setEnabled(enable);
}

void TaskLeaderLine::setUiEdit()
{
    enableVPUi(true);
    setWindowTitle(QObject::tr("Edit Leader Line"));

    if (m_lineFeat) {
        std::string baseName = m_lineFeat->LeaderParent.getValue()->getNameInDocument();
        ui->tbBaseView->setText(QString::fromStdString(baseName));

        DrawGuiUtil::loadArrowBox(ui->cboxStartSym);
        ui->cboxStartSym->setCurrentIndex(m_lineFeat->StartSymbol.getValue());
        connect(ui->cboxStartSym, qOverload<int>(&QComboBox::currentIndexChanged), this, &TaskLeaderLine::onStartSymbolChanged);
        DrawGuiUtil::loadArrowBox(ui->cboxEndSym);
        ui->cboxEndSym->setCurrentIndex(m_lineFeat->EndSymbol.getValue());
        connect(ui->cboxEndSym, qOverload<int>(&QComboBox::currentIndexChanged), this, &TaskLeaderLine::onEndSymbolChanged);

        ui->pbTracker->setText(tr("Edit Points"));
        if (m_vpp->getMDIViewPage()) {
            ui->pbTracker->setEnabled(true);
            ui->pbCancelEdit->setEnabled(true);
        } else {
            ui->pbTracker->setEnabled(false);
            ui->pbCancelEdit->setEnabled(false);
        }
    }

    if (m_lineVP) {
        ui->cpLineColor->setColor(m_lineVP->Color.getValue().asValue<QColor>());
        ui->dsbWeight->setValue(m_lineVP->LineWidth.getValue());
        ui->cboxStyle->setCurrentIndex(m_lineVP->LineStyle.getValue());
    }
    connect(ui->cpLineColor, &ColorButton::changed, this, &TaskLeaderLine::onColorChanged);
    ui->dsbWeight->setMinimum(0);
    connect(ui->dsbWeight, qOverload<double>(&QuantitySpinBox::valueChanged), this, &TaskLeaderLine::onLineWidthChanged);
    connect(ui->cboxStyle, qOverload<int>(&QComboBox::currentIndexChanged), this, &TaskLeaderLine::onLineStyleChanged);
}

void TaskLeaderLine::recomputeFeature()
{
    App::DocumentObject* objVP = m_lineVP->getObject();
    assert(objVP);
    objVP->recomputeFeature();
}

void TaskLeaderLine::onStartSymbolChanged()
{
    m_lineFeat->StartSymbol.setValue(ui->cboxStartSym->currentIndex());
    recomputeFeature();
}

void TaskLeaderLine::onEndSymbolChanged()
{
    m_lineFeat->EndSymbol.setValue(ui->cboxEndSym->currentIndex());
    recomputeFeature();
}

void TaskLeaderLine::onColorChanged()
{
    Base::Color ac;
    ac.setValue<QColor>(ui->cpLineColor->color());
    m_lineVP->Color.setValue(ac);
    recomputeFeature();
}

void TaskLeaderLine::onLineWidthChanged()
{
    m_lineVP->LineWidth.setValue(ui->dsbWeight->rawValue());
    recomputeFeature();
}

void TaskLeaderLine::onLineStyleChanged()
{
    m_lineVP->LineStyle.setValue(ui->cboxStyle->currentIndex());
    recomputeFeature();
}


//******************************************************************************
//! pagePoints are absolute page positions in millimetres with conventional Y.
void TaskLeaderLine::createLeaderFeature(
    const std::vector<Base::Vector3d>& pagePoints)
{
    auto* document =
        m_basePage ? m_basePage->getDocument() : nullptr;
    if (!document || !m_baseFeat
        || m_baseFeat->getDocument() != document
        || m_baseFeat->findParentPage() != m_basePage) {
        throw Base::RuntimeError(
            "The leader target is no longer available"
        );
    }
    TaskInternal::OwnedDocumentTransaction transaction(
        document,
        QT_TRANSLATE_NOOP("Command", "Create Leader")
    );

    const DrawingLeaderDefaults defaults = drawingLeaderDefaults();
    DrawingLeaderStyle style = defaults.style;
    style.startSymbol = ui->cboxStartSym->currentIndex();
    style.endSymbol = ui->cboxEndSym->currentIndex();
    style.lineWidthMm = ui->dsbWeight->rawValue();
    style.lineStyle = ui->cboxStyle->currentIndex();
    style.lineColor.setValue<QColor>(ui->cpLineColor->color());
    const std::string label = QCoreApplication::translate(
        "DrawLeaderLine",
        "LeaderLine").toStdString();
    m_lineFeat = createDrawingLeaderLine(
        m_basePage,
        m_baseFeat,
        pagePoints,
        label,
        style);
    m_leaderName = m_lineFeat->getNameInDocument();
    TaskInternal::updateExactDocument(document);
    transaction.commit();
}

void TaskLeaderLine::dumpTrackerPoints(std::vector<Base::Vector3d>& tPoints) const
{
    Base::Console().message("TTL::dumpTrackerPoints(%d)\n", tPoints.size());
    Base::Console().message("TTL::dumpTrackerPoints - attach point: %s\n", DU::formatVector(m_attachPoint).c_str());
    for (auto& point : tPoints) {
        Base::Console().message("TTL::dumpTrackerPoints - a point: %s\n", DU::formatVector(point).c_str());
    }
}

void TaskLeaderLine::updateLeaderFeature()
{
    if (!m_lineFeat || !m_lineVP
        || m_lineFeat->getDocument()->getBookedTransactionID()
            == App::NullTransaction) {
        throw Base::RuntimeError(
            "The leader edit transaction is no longer available"
        );
    }
    //waypoints & x, y are updated by QGILeaderLine (for edits only!)
    commonFeatureUpdate();
    Base::Color ac;
    ac.setValue<QColor>(ui->cpLineColor->color());
    m_lineVP->Color.setValue(ac);
    m_lineVP->LineWidth.setValue(ui->dsbWeight->rawValue());
    m_lineVP->LineStyle.setValue(ui->cboxStyle->currentIndex());

    m_lineFeat->recomputeFeature();
    if (m_lineFeat->isError()) {
        throw Base::RuntimeError(
            "The leader line could not produce a valid result"
        );
    }
    TaskInternal::updateExactDocument(
        m_lineFeat->getDocument()
    );

    if (m_baseFeat) {
        m_baseFeat->requestPaint();
    }
    m_lineFeat->requestPaint();
}

void TaskLeaderLine::commonFeatureUpdate()
{
    int start = ui->cboxStartSym->currentIndex();
    int end   = ui->cboxEndSym->currentIndex();
    m_lineFeat->StartSymbol.setValue(start);
    m_lineFeat->EndSymbol.setValue(end);
}

void TaskLeaderLine::removeFeature()
{
//    Base::Console().message("TTL::removeFeature()\n");
    if (!m_lineFeat) {
        return;
    }

    if (m_createMode) {
        try {
            App::Document* document = m_lineFeat->getDocument();
            if (!document
                || !m_basePage
                || m_basePage->getDocument() != document
                || m_lineFeat->findParentPage() != m_basePage) {
                return;
            }
            const std::string leaderName =
                m_lineFeat->getNameInDocument();
            m_basePage->removeView(m_lineFeat);
            document->removeObject(leaderName.c_str());
            m_lineFeat = nullptr;
        }
        catch (...) {
            Base::Console().message("TTL::removeFeature - failed to delete feature\n");
            return;
        }
    }
    else {
        restoreState();
    }
}

//********** Tracker routines *******************************************************************
void TaskLeaderLine::onTrackerClicked(bool clicked)
{
    Q_UNUSED(clicked);
    if (!m_vpp->getMDIViewPage()) {
        Base::Console().message("TLL::onTrackerClicked - no MDI, no tracker!\n");
        return;
    }

    if ( m_pbTrackerState == TrackerAction::SAVE &&
         getCreateMode() ){
        if (m_tracker) {
            m_tracker->terminateDrawing();
        }
        m_pbTrackerState = TrackerAction::PICK;
        ui->pbTracker->setText(tr("Pick Points"));
        ui->pbCancelEdit->setEnabled(false);
        enableTaskButtons(true);

        setEditCursor(Qt::ArrowCursor);
        return;
    }

    if ( m_pbTrackerState == TrackerAction::SAVE &&
         !getCreateMode() ) {                //edit mode
        if (m_qgLeader) {
            m_qgLeader->closeEdit();
        }
        m_pbTrackerState = TrackerAction::PICK;
        ui->pbTracker->setText(tr("Edit Points"));
        ui->pbCancelEdit->setEnabled(false);
        enableTaskButtons(true);

        setEditCursor(Qt::ArrowCursor);
        return;
    }

    //TrackerAction::PICK or TrackerAction::EDIT
    if (getCreateMode()) {
        m_inProgressLock = true;
        m_saveContextPolicy = m_vpp->getMDIViewPage()->contextMenuPolicy();
        m_vpp->getMDIViewPage()->setContextMenuPolicy(Qt::PreventContextMenu);
        m_trackerMode = QGTracker::TrackerMode::Line;
        setEditCursor(Qt::CrossCursor);
        startTracker();

        QString msg = tr("Pick a starting point for leader line");
        getMainWindow()->statusBar()->show();
        Gui::getMainWindow()->showMessage(msg, MessageDisplayTime);
        ui->pbTracker->setText(tr("Save Points"));
        ui->pbTracker->setEnabled(true);
        ui->pbCancelEdit->setEnabled(true);
        m_pbTrackerState = TrackerAction::SAVE;
        enableTaskButtons(false);
    } else {    //edit mode
        // pageDeltas are in mm with conventional Y axis
        auto pageDeltas =  m_lineFeat->getScaledAndRotatedPoints();

        m_sceneDeltas.clear();
        m_sceneDeltas.reserve(pageDeltas.size());
        // now convert to sceneUnits and Qt Y axis
        for (auto& entry : pageDeltas) {
            m_sceneDeltas.push_back(DGU::toSceneCoords(entry, false));
        }

        if (!m_sceneDeltas.empty()) {    //regular edit session
            m_inProgressLock = true;
            m_saveContextPolicy = m_vpp->getMDIViewPage()->contextMenuPolicy();
            m_vpp->getMDIViewPage()->setContextMenuPolicy(Qt::PreventContextMenu);
            QGIView* qgiv = m_vpp->getQGSPage()->findQViewForDocObj(m_lineFeat);
            auto qgLead = dynamic_cast<QGILeaderLine*>(qgiv);

            if (!qgLead) {
                //tarfu
                Base::Console().error("TaskLeaderLine - cannot find leader graphic\n");
                //now what? throw will generate "unknown unhandled exception"
            } else {
                m_qgLeader = qgLead;
                connect(qgLead, &QGILeaderLine::editComplete,
                        this, &TaskLeaderLine::onPointEditComplete);
                qgLead->startPathEdit();
                QString msg = tr("Click and drag markers to adjust leader line");
                getMainWindow()->statusBar()->show();
                Gui::getMainWindow()->showMessage(msg, MessageDisplayTime);
                ui->pbTracker->setText(tr("Save Changes"));
                ui->pbTracker->setEnabled(true);
                ui->pbCancelEdit->setEnabled(true);
                m_pbTrackerState = TrackerAction::SAVE;
                enableTaskButtons(false);
            }
        } else { // need to recreate leaderline
            m_inProgressLock = true;
            m_saveContextPolicy = m_vpp->getMDIViewPage()->contextMenuPolicy();
            m_vpp->getMDIViewPage()->setContextMenuPolicy(Qt::PreventContextMenu);
            m_trackerMode = QGTracker::TrackerMode::Line;
            setEditCursor(Qt::CrossCursor);
            startTracker();

            QString msg = tr("Pick a starting point for leader line");
            getMainWindow()->statusBar()->show();
            Gui::getMainWindow()->showMessage(msg, MessageDisplayTime);
            ui->pbTracker->setText(tr("Save Changes"));
            ui->pbTracker->setEnabled(true);
            ui->pbCancelEdit->setEnabled(true);
            m_pbTrackerState = TrackerAction::SAVE;
            enableTaskButtons(false);
        }
    }
}

void TaskLeaderLine::startTracker()
{
//    Base::Console().message("TTL::startTracker()\n");
    if (!m_vpp->getQGSPage()) {
        return;
    }

    if (m_trackerMode == QGTracker::TrackerMode::None) {
        return;
    }

    if (!m_tracker) {
        m_tracker = new QGTracker(m_vpp->getQGSPage(), m_trackerMode);
        connect(
            m_tracker,
            &QObject::destroyed,
            this,
            [this]() { m_tracker = nullptr; }
        );
        QObject::connect(
            m_tracker, &QGTracker::drawingFinished,
            this     , &TaskLeaderLine::onTrackerFinished
           );
    } else {
        //this is too harsh. but need to avoid restarting process
        throw Base::RuntimeError("TechDrawNewLeader - tracker already active\n");
    }
    setEditCursor(Qt::CrossCursor);
    QString msg = tr("Left click to set a point");
    Gui::getMainWindow()->statusBar()->show();
    Gui::getMainWindow()->showMessage(msg, MessageDisplayTime);
}

void TaskLeaderLine::onTrackerFinished(std::vector<QPointF> trackerScenePoints, QGIView* qgParent)
{
    //in this case, we already know who the parent is.  We don't need QGTracker to tell us.
    (void) qgParent;
    //    Base::Console().message("TTL::onTrackerFinished() - parent: %X\n", qgParent);
    if (trackerScenePoints.empty()) {
        Base::Console().error("TaskLeaderLine - no points available\n");
        return;
    }

    if (m_qgParent && getCreateMode()) {
        m_pagePoints.clear();
        m_pagePoints.reserve(trackerScenePoints.size());
        for (const auto& point : trackerScenePoints) {
            m_pagePoints.emplace_back(
                Rez::appX(point.x()),
                -Rez::appX(point.y()),
                0.0);
        }
    }
    else if (m_qgParent) {
        double scale = m_qgParent->getScale();
        QPointF mapped = m_qgParent->mapFromScene(trackerScenePoints.front()) / scale;
        m_attachPoint = Base::Vector3d(mapped.x(), mapped.y(), 0.0);
        m_sceneDeltas = scenePointsToDeltas(trackerScenePoints);
    } else {
        Base::Console().message("TTL::onTrackerFinished - cannot find parent graphic!\n");
        //blow up!?
        throw Base::RuntimeError("TaskLeaderLine - cannot find parent graphic");
    }

    QString msg = tr("Press OK or Cancel to continue");
    getMainWindow()->statusBar()->show();
    Gui::getMainWindow()->showMessage(msg, MessageDisplayTime);
    enableTaskButtons(true);

            // ??? why does the tracker go to sleep when we are finished with it? why not removeTracker
    m_tracker->sleep(true);
    m_inProgressLock = false;

            // can not pick points any more?
    ui->pbTracker->setEnabled(false);
    ui->pbCancelEdit->setEnabled(false);

    // only option available to user is accept/reject?
    enableTaskButtons(true);
    setEditCursor(Qt::ArrowCursor);  // already done by m_tracker->sleep()?
}


// this is called at every possible exit path?
void TaskLeaderLine::removeTracker()
{
    if (!m_tracker) {
        return;
    }
    // Deleting a QGraphicsItem removes it from its scene.  This remains safe
    // even if the page view has already been rebuilt.
    delete m_tracker;
    m_tracker = nullptr;
}

void TaskLeaderLine::onCancelEditClicked(bool clicked)
{
    Q_UNUSED(clicked);
//    Base::Console().message("TTL::onCancelEditClicked() m_pbTrackerState: %d\n",
//                            m_pbTrackerState);
    abandonEditSession();
    if (m_lineFeat) {
        m_lineFeat->requestPaint();
    }

    m_pbTrackerState = TrackerAction::EDIT;
    ui->pbTracker->setText(tr("Edit Points"));
    ui->pbCancelEdit->setEnabled(false);
    enableTaskButtons(true);

    m_inProgressLock = false;
    setEditCursor(Qt::ArrowCursor);
}

QGIView* TaskLeaderLine::findParentQGIV()
{
    if (!m_baseFeat) {
        return nullptr;
    }

    Gui::ViewProvider* gvp = QGIView::getViewProvider(m_baseFeat);
    ViewProviderDrawingView* vpdv = freecad_cast<ViewProviderDrawingView*>(gvp);
    if (!vpdv) {
        return nullptr;
    }

    return vpdv->getQView();;
}

void TaskLeaderLine::setEditCursor(const QCursor &cursor)
{
    if (!m_vpp->getQGSPage()) {
        return;
    }
    if (m_baseFeat) {
        QGIView* qgivBase = m_vpp->getQGSPage()->findQViewForDocObj(m_baseFeat);
        if (qgivBase) {
            qgivBase->setCursor(cursor);
        }
    }
}

// from scene QPointF to zero origin (delta from p0) Vector3d points
std::vector<Base::Vector3d> TaskLeaderLine::scenePointsToDeltas(std::vector<QPointF> scenePoints)
{
    if (scenePoints.empty()) {
        return {};
    }

    std::vector<Base::Vector3d> result;
    auto frontPoint = DU::toVector3d(m_qgParent->mapFromScene(scenePoints.front()));
    result.reserve(scenePoints.size());
    for (auto& point: scenePoints) {
        auto viewPoint = m_qgParent->mapFromScene(point);
        auto vPoint = DU::toVector3d(viewPoint);
        auto delta = vPoint - frontPoint;
        auto rotationDeg = m_baseFeat->Rotation.getValue();
        auto deltaUnrotated{delta};
        if (rotationDeg != 0) {
            deltaUnrotated = DU::invertY(deltaUnrotated);
            deltaUnrotated.RotateZ(-Base::toRadians(rotationDeg));
            deltaUnrotated = DU::invertY(deltaUnrotated);
        }

        result.push_back(deltaUnrotated);
    }
    return result;
}

//******************************************************************************
//void TaskLeaderLine::onPointEditComplete(std::vector<QPointF> pts, QGIView* parent)

//! point edit session completed.  reset ui to initial state.
void TaskLeaderLine::onPointEditComplete()
{
//    Base::Console().message("TTL::onPointEditComplete()\n");
    m_inProgressLock = false;

    m_pbTrackerState = TrackerAction::EDIT;
    ui->pbTracker->setText(tr("Edit Points"));
    ui->pbTracker->setEnabled(true);
    ui->pbCancelEdit->setEnabled(true);
    enableTaskButtons(true);
}


//! give up on the current point editing session.  reset the ui so we are in a state to
//! start editing points again.  leave the existing tracker instance in place.
void TaskLeaderLine::abandonEditSession()
{
//    Base::Console().message("TTL::abandonEditSession()\n");
    constexpr int MessageDuration{4000};
    if (m_qgLeader) {
        // tell the graphics item that we are giving up so it should do any clean up it needs.
        m_qgLeader->abandonEdit();
    }
    QString msg = tr("In progress edit abandoned. Start over.");
    getMainWindow()->statusBar()->show();
    Gui::getMainWindow()->showMessage(msg, MessageDuration);

    m_pbTrackerState = TrackerAction::EDIT;
    ui->pbTracker->setText(tr("Edit Points"));
    enableTaskButtons(true);
    ui->pbTracker->setEnabled(true);
    ui->pbCancelEdit->setEnabled(false);

    setEditCursor(Qt::ArrowCursor);
}

void TaskLeaderLine::saveButtons(QPushButton* btnOK,
                             QPushButton* btnCancel)
{
    m_btnOK = btnOK;
    m_btnCancel = btnCancel;
}

void TaskLeaderLine::enableTaskButtons(bool enable)
{
    m_btnOK->setEnabled(enable);
    m_btnCancel->setEnabled(enable);
}

//******************************************************************************

bool TaskLeaderLine::accept()
{
//    Base::Console().message("TTL::accept()\n");
    if (m_inProgressLock) {
        //accept() button shouldn't be available if there is an edit in progress.
        abandonEditSession();
        removeTracker();
        return false;
    }

    auto* document =
        m_basePage ? m_basePage->getDocument() : nullptr;
    Gui::Document* guiDocument =
        document && Gui::Application::Instance
        ? Gui::Application::Instance->getDocument(document)
        : nullptr;
    if (!guiDocument) {
        return false;
    }
    if (getCreateMode() && m_pagePoints.size() < 2) {
        QMessageBox::warning(
            this,
            tr("Leader Points Required"),
            tr("Pick at least two points for the leader line.")
        );
        return false;
    }

    try {
        if (!getCreateMode()) {
            updateLeaderFeature();
        }
        else {
            createLeaderFeature(m_pagePoints);
        }
    }
    catch (const Base::Exception& error) {
        if (getCreateMode()) {
            m_lineFeat = nullptr;
        }
        QMessageBox::critical(
            this,
            tr("Leader Update Failed"),
            QString::fromUtf8(error.what())
        );
        return false;
    }
    m_trackerMode = QGTracker::TrackerMode::None;
    removeTracker();

    TaskInternal::resetExactEdit(document);

    if (m_vpp->getMDIViewPage())
        m_vpp->getMDIViewPage()->setContextMenuPolicy(m_saveContextPolicy);

    return true;
}

bool TaskLeaderLine::reject()
{
    if (m_inProgressLock) {
//        Base::Console().message("TTL::reject - edit in progress!!\n");
        //reject() button shouldn't be available if there is an edit in progress.
        abandonEditSession();
        removeTracker();
        return false;
    }

    auto* document =
        m_basePage ? m_basePage->getDocument() : nullptr;

    m_trackerMode = QGTracker::TrackerMode::None;
    removeTracker();

    // Creation has not changed the model. Editing is owned by TaskView, which
    // rolls its exact transaction back after this panel is torn down.
    TaskInternal::resetExactEdit(document);

    if (m_vpp->getMDIViewPage()) {
        m_vpp->getMDIViewPage()->setContextMenuPolicy(m_saveContextPolicy);
    }

    return true;
}

/////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
TaskDlgLeaderLine::TaskDlgLeaderLine(TechDraw::DrawView* baseFeat,
                                     TechDraw::DrawPage* page)
    : TaskDialog()
{
    widget  = new TaskLeaderLine(baseFeat, page);
    taskbox = new Gui::TaskView::TaskBox(Gui::BitmapFactory().pixmap("actions/TechDraw_LeaderLine"),
                                             widget->windowTitle(), true, nullptr);
    taskbox->groupLayout()->addWidget(widget);
    Content.push_back(taskbox);
    setAutoCloseOnTransactionChange(true);
}

TaskDlgLeaderLine::TaskDlgLeaderLine(TechDrawGui::ViewProviderLeader* leadVP)
    : TaskDialog()
{
    widget  = new TaskLeaderLine(leadVP);
    taskbox = new Gui::TaskView::TaskBox(Gui::BitmapFactory().pixmap("actions/TechDraw_LeaderLine"),
                                             widget->windowTitle(), true, nullptr);
    taskbox->groupLayout()->addWidget(widget);
    Content.push_back(taskbox);
    setAutoCloseOnTransactionChange(true);
}

TaskDlgLeaderLine::~TaskDlgLeaderLine()
{
}

void TaskDlgLeaderLine::update()
{
//    widget->updateTask();
}

void TaskDlgLeaderLine::modifyStandardButtons(QDialogButtonBox* box)
{
    QPushButton* btnOK = box->button(QDialogButtonBox::Ok);
    QPushButton* btnCancel = box->button(QDialogButtonBox::Cancel);
    widget->saveButtons(btnOK, btnCancel);
}

//==== calls from the TaskView ===============================================================
void TaskDlgLeaderLine::open()
{
}

void TaskDlgLeaderLine::clicked(int)
{
}

bool TaskDlgLeaderLine::accept()
{
    return widget->accept();
}

bool TaskDlgLeaderLine::reject()
{
    return widget->reject();
}

#include <Mod/TechDraw/Gui/moc_TaskLeaderLine.cpp>
