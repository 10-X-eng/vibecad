// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2013 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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

#include <Precision.hxx>
#include <QDoubleValidator>
#include <QLocale>
#include <QMessageBox>
#include <algorithm>
#include <array>
#include <exception>

#include <Inventor/nodes/SoBaseColor.h>
#include <Inventor/nodes/SoCoordinate3.h>
#include <Inventor/nodes/SoDrawStyle.h>
#include <Inventor/nodes/SoMarkerSet.h>
#include <Inventor/nodes/SoSeparator.h>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <Base/Exception.h>
#include <Gui/Application.h>
#include <Gui/CommandT.h>
#include <Gui/ExactTransaction.h>
#include <Gui/Inventor/MarkerBitmaps.h>
#include <Gui/Notifications.h>
#include <Gui/TaskView/TaskView.h>
#include <Gui/ViewProvider.h>
#include <Gui/WaitCursor.h>
#include <Mod/Sketcher/App/SketchObject.h>

#include "TaskSketcherValidation.h"
#include "ui_TaskSketcherValidation.h"


using namespace SketcherGui;
using namespace Gui::TaskView;

/* TRANSLATOR SketcherGui::SketcherValidation */

SketcherValidation::SketcherValidation(Sketcher::SketchObject* Obj, QWidget* parent)
    : QWidget(parent)
    , ui(new Ui_TaskSketcherValidation())
    , sketch(Obj)
    , exactDocument(Obj ? Obj->getDocument() : nullptr)
    , exactDocumentName(
          exactDocument ? exactDocument->getName() : std::string()
      )
    , exactDocumentUid(
          exactDocument
              ? exactDocument->Uid.getValueStr()
              : std::string()
      )
    , exactSketchId(Obj ? Obj->getID() : 0)
    , exactSketchName(
          Obj && Obj->getNameInDocument()
              ? Obj->getNameInDocument()
              : std::string()
      )
    , coincidenceRoot(nullptr)
{
    ui->setupUi(this);
    setupConnections();

    ui->fixButton->setEnabled(false);
    ui->fixConstraint->setEnabled(false);
    ui->fixDegenerated->setEnabled(false);
    ui->swapReversed->setEnabled(false);
    ui->checkBoxIgnoreConstruction->setEnabled(true);
    std::array tolerances = {
        // NOLINTBEGIN
        Precision::Confusion() / 100.0,
        Precision::Confusion() / 10.0,
        Precision::Confusion(),
        Precision::Confusion() * 10.0,
        Precision::Confusion() * 100.0,
        Precision::Confusion() * 1000.0,
        Precision::Confusion() * 10000.0,
        Precision::Confusion() * 100000.0
        // NOLINTEND
    };

    QLocale loc;
    for (double it : tolerances) {
        ui->comboBoxTolerance->addItem(loc.toString(it), QVariant(it));
    }
    ui->comboBoxTolerance->setCurrentIndex(5);
    ui->comboBoxTolerance->setEditable(true);
    const double bottom = 0.0;
    const double top = 10.0;
    const int decimals = 10;
    ui->comboBoxTolerance->setValidator(new QDoubleValidator(bottom, top, decimals, this));
}

SketcherValidation::~SketcherValidation()
{
    hidePoints();
}

Sketcher::SketchObject* SketcherValidation::resolveExactSketch(
    bool requireCurrentHistory
) const
{
    if (!exactDocument || exactDocumentName.empty()
        || exactDocumentUid.empty() || exactSketchId <= 0
        || exactSketchName.empty()) {
        return nullptr;
    }
    auto* document =
        App::GetApplication().getDocument(exactDocumentName.c_str());
    if (document != exactDocument
        || document->Uid.getValueStr() != exactDocumentUid) {
        return nullptr;
    }
    auto* object = document->getObjectByID(exactSketchId);
    if (!object || !object->getNameInDocument()
        || exactSketchName != object->getNameInDocument()
        || document->getObject(exactSketchName.c_str()) != object
        || sketch.expired() || sketch.get() != object) {
        return nullptr;
    }
    auto* exactSketch =
        freecad_cast<Sketcher::SketchObject*>(object);
    if (!exactSketch) {
        return nullptr;
    }
    if (requireCurrentHistory) {
        try {
            if (!App::DocumentTimeline::
                    isObjectUsableAtCurrentPosition(exactSketch)) {
                return nullptr;
            }
        }
        catch (...) {
            return nullptr;
        }
    }
    return exactSketch;
}

bool SketcherValidation::runExactMutation(
    const char* transactionName,
    const std::function<void(Sketcher::SketchObject&)>& mutation,
    bool recompute
)
{
    auto* exactSketch = resolveExactSketch();
    auto* document =
        exactSketch ? exactSketch->getDocument() : nullptr;
    if (!exactSketch || !document) {
        return false;
    }
    if (document->getBookedTransactionID()
            != App::NullTransaction
        || document->hasPendingTransaction()) {
        Gui::TranslatedUserWarning(
            exactSketch,
            tr("Sketch validation is busy"),
            tr("Another modeling operation already owns this document.")
        );
        return false;
    }

    try {
        Gui::ExactTransaction transaction(
            *document,
            transactionName
        );
        mutation(*exactSketch);
        if (resolveExactSketch(false) != exactSketch) {
            throw Base::RuntimeError(
                "The validated Sketch changed during the operation"
            );
        }
        if (recompute) {
            document->recompute();
        }
        if (resolveExactSketch(false) != exactSketch) {
            throw Base::RuntimeError(
                "The validated Sketch changed before the operation completed"
            );
        }
        if (!transaction.commit()) {
            throw Base::RuntimeError(
                "The Sketch validation transaction could not be committed"
            );
        }
        return true;
    }
    catch (const Base::Exception& error) {
        Gui::TranslatedUserWarning(
            exactDocumentName,
            tr("Sketch validation failed"),
            QString::fromUtf8(error.what())
        );
    }
    catch (const std::exception& error) {
        Gui::TranslatedUserWarning(
            exactDocumentName,
            tr("Sketch validation failed"),
            QString::fromUtf8(error.what())
        );
    }
    return false;
}

void SketcherValidation::setupConnections()
{
    // clang-format off
    connect(ui->findButton, &QPushButton::clicked,
            this, &SketcherValidation::onFindButtonClicked);
    connect(ui->fixButton, &QPushButton::clicked,
            this, &SketcherValidation::onFixButtonClicked);
    connect(ui->highlightButton, &QPushButton::clicked,
            this, &SketcherValidation::onHighlightButtonClicked);
    connect(ui->findConstraint, &QPushButton::clicked,
            this, &SketcherValidation::onFindConstraintClicked);
    connect(ui->fixConstraint, &QPushButton::clicked,
            this, &SketcherValidation::onFixConstraintClicked);
    connect(ui->findReversed, &QPushButton::clicked,
            this, &SketcherValidation::onFindReversedClicked);
    connect(ui->swapReversed, &QPushButton::clicked,
            this, &SketcherValidation::onSwapReversedClicked);
    connect(ui->orientLockEnable, &QPushButton::clicked,
            this, &SketcherValidation::onOrientLockEnableClicked);
    connect(ui->orientLockDisable, &QPushButton::clicked,
            this, &SketcherValidation::onOrientLockDisableClicked);
    connect(ui->delConstrExtr, &QPushButton::clicked,
            this, &SketcherValidation::onDelConstrExtrClicked);
    connect(ui->findDegenerated, &QPushButton::clicked,
            this, &SketcherValidation::onFindDegeneratedClicked);
    connect(ui->fixDegenerated, &QPushButton::clicked,
            this, &SketcherValidation::onFixDegeneratedClicked);
    // clang-format on
}

void SketcherValidation::changeEvent(QEvent* e)
{
    if (e->type() == QEvent::LanguageChange) {
        ui->retranslateUi(this);
    }
    QWidget::changeEvent(e);
}

void SketcherValidation::onFindButtonClicked()
{
    auto* exactSketch = resolveExactSketch();
    if (!exactSketch) {
        return;
    }

    double prec = Precision::Confusion();
    bool ok {};
    double conv {};

    conv = QLocale::system().toDouble(ui->comboBoxTolerance->currentText(), &ok);

    if (ok) {
        prec = conv;
    }
    else {
        QVariant v = ui->comboBoxTolerance->itemData(ui->comboBoxTolerance->currentIndex());
        if (v.isValid()) {
            prec = v.toDouble();
        }
    }

    exactSketch->detectMissingPointOnPointConstraints(
        prec,
        !ui->checkBoxIgnoreConstruction->isChecked()
    );

    std::vector<Sketcher::ConstraintIds>& vertexConstraints
        = exactSketch->getMissingPointOnPointConstraints();

    std::vector<Base::Vector3d> points;
    points.reserve(vertexConstraints.size());

    for (auto vc : vertexConstraints) {
        points.push_back(vc.v);
    }

    hidePoints();
    if (vertexConstraints.empty()) {
        Gui::TranslatedNotification(
            exactSketch,
            tr("No missing coincidences"),
            tr("No missing coincidences found")
        );

        ui->fixButton->setEnabled(false);
    }
    else {
        showPoints(points);
        Gui::TranslatedUserWarning(
            exactSketch,
            tr("Missing coincidences"),
            tr("%1 missing coincidences found").arg(vertexConstraints.size())
        );

        ui->fixButton->setEnabled(true);
    }
}

void SketcherValidation::onFixButtonClicked()
{
    if (!resolveExactSketch()) {
        return;
    }

    Gui::WaitCursor wait;
    if (!runExactMutation(
            "Add coincident constraint",
            [](Sketcher::SketchObject& exactSketch) {
                Gui::cmdAppObjectArgs(
                    &exactSketch,
                    "makeMissingPointOnPointCoincident()"
                );
            },
            true
        )) {
        return;
    }

    ui->fixButton->setEnabled(false);
    hidePoints();
}

void SketcherValidation::onHighlightButtonClicked()
{
    auto* exactSketch = resolveExactSketch();
    if (!exactSketch) {
        return;
    }

    std::vector<Base::Vector3d> points;

    points = exactSketch->getOpenVertices();

    hidePoints();
    if (!points.empty()) {
        showPoints(points);
    }
}

void SketcherValidation::onFindConstraintClicked()
{
    auto* exactSketch = resolveExactSketch();
    if (!exactSketch) {
        return;
    }

    if (exactSketch->evaluateConstraints()) {
        Gui::TranslatedNotification(
            exactSketch,
            tr("No invalid constraints"),
            tr("No invalid constraints found")
        );

        ui->fixConstraint->setEnabled(false);
    }
    else {
        Gui::TranslatedUserError(
            exactSketch,
            tr("Invalid constraints"),
            tr("Invalid constraints found")
        );

        ui->fixConstraint->setEnabled(true);
    }
}

void SketcherValidation::onFixConstraintClicked()
{
    if (!resolveExactSketch()) {
        return;
    }

    if (runExactMutation(
            "Validate sketch constraints",
            [](Sketcher::SketchObject& exactSketch) {
                Gui::cmdAppObjectArgs(
                    &exactSketch,
                    "validateConstraints()"
                );
            },
            true
        )) {
        ui->fixConstraint->setEnabled(false);
    }
}

void SketcherValidation::onFindReversedClicked()
{
    auto* exactSketch = resolveExactSketch();
    if (!exactSketch) {
        return;
    }

    std::vector<Base::Vector3d> points;
    const std::vector<Part::Geometry*>& geom =
        exactSketch->getExternalGeometry();
    for (const auto geo : geom) {
        // only arcs of circles need to be repaired. Arcs of ellipse were so broken there should be
        // nothing to repair from.
        if (const auto segm = dynamic_cast<const Part::GeomArcOfCircle*>(geo)) {
            if (segm->isReversed()) {
                points.push_back(segm->getStartPoint(/*emulateCCWXY=*/true));
                points.push_back(segm->getEndPoint(/*emulateCCWXY=*/true));
            }
        }
    }
    hidePoints();
    if (!points.empty()) {
        int nc =
            exactSketch->port_reversedExternalArcs(
                /*justAnalyze=*/true
            );
        showPoints(points);
        if (nc > 0) {
            Gui::TranslatedUserWarning(
                exactSketch,
                tr("Reversed external geometry"),
                tr("%1 reversed external geometry arcs were found. Their endpoints are"
                   " encircled in the 3D view.\n\n"
                   "%2 constraints are linking to the endpoints. The constraints have"
                   " been listed in the report view (menu View -> Panels -> Report view).\n\n"
                   "Click \"Swap endpoints in constraints\" button to reassign endpoints."
                   " Do this only once to sketches created in FreeCAD older than v0.15")
                    .arg(points.size() / 2)
                    .arg(nc)
            );

            ui->swapReversed->setEnabled(true);
        }
        else {
            Gui::TranslatedUserWarning(
                exactSketch,
                tr("Reversed external geometry"),
                tr("%1 reversed external geometry arcs were found. Their endpoints are "
                   "encircled in the 3D view.\n\n"
                   "However, no constraints linking to the endpoints were found.")
                    .arg(points.size() / 2)
            );

            ui->swapReversed->setEnabled(false);
        }
    }
    else {
        Gui::TranslatedNotification(
            exactSketch,
            tr("Reversed external geometry"),
            tr("No reversed external geometry arcs were found.")
        );
    }
}

void SketcherValidation::onSwapReversedClicked()
{
    if (!resolveExactSketch()) {
        return;
    }

    int changes = 0;
    if (!runExactMutation(
            "Sketch porting",
            [&changes](Sketcher::SketchObject& exactSketch) {
                changes = exactSketch.port_reversedExternalArcs(
                    /*justAnalyze=*/false
                );
            },
            true
        )) {
        return;
    }
    auto* exactSketch = resolveExactSketch();
    if (!exactSketch) {
        return;
    }
    Gui::TranslatedNotification(
        exactSketch,
        tr("Reversed external geometry"),
        tr("%1 changes were made to constraints linking to endpoints of reversed arcs.")
            .arg(changes)
    );

    hidePoints();
    ui->swapReversed->setEnabled(false);
}

void SketcherValidation::onOrientLockEnableClicked()
{
    if (!resolveExactSketch()) {
        return;
    }

    int changes = 0;
    if (!runExactMutation(
            "Constraint orientation lock",
            [&changes](Sketcher::SketchObject& exactSketch) {
                changes = exactSketch.changeConstraintsLocking(
                    /*bLock=*/true
                );
            },
            true
        )) {
        return;
    }
    auto* exactSketch = resolveExactSketch();
    if (!exactSketch) {
        return;
    }
    Gui::TranslatedNotification(
        exactSketch,
        tr("Constraint orientation locking"),
        tr("Orientation locking was enabled and recomputed for %1 constraints. The"
           " constraints have been listed in the report view (menu View → Panels →"
           " Report view).")
            .arg(changes)
    );
}

void SketcherValidation::onOrientLockDisableClicked()
{
    if (!resolveExactSketch()) {
        return;
    }

    int changes = 0;
    if (!runExactMutation(
            "Constraint orientation unlock",
            [&changes](Sketcher::SketchObject& exactSketch) {
                changes = exactSketch.changeConstraintsLocking(
                    /*bLock=*/false
                );
            },
            true
        )) {
        return;
    }
    auto* exactSketch = resolveExactSketch();
    if (!exactSketch) {
        return;
    }
    Gui::TranslatedNotification(
        exactSketch,
        tr("Constraint orientation locking"),
        tr("Orientation locking was disabled for %1 constraints. The"
           " constraints have been listed in the report view (menu View → Panels →"
           " Report view). Note that for all future constraints, the locking still"
           " defaults to ON.")
            .arg(changes)
    );
}

void SketcherValidation::onDelConstrExtrClicked()
{
    if (!resolveExactSketch()) {
        return;
    }

    int reply = QMessageBox::question(
        this,
        tr("Delete Constraints to External Geometry"),
        tr("This will delete all constraints that deal with external geometry. This is "
           "useful to rescue a sketch with broken or changed links to external geometry. Delete "
           "the constraints?"),
        QMessageBox::No | QMessageBox::Yes,
        QMessageBox::No
    );
    if (reply != QMessageBox::Yes) {
        return;
    }

    if (!runExactMutation(
            "Delete constraints",
            [](Sketcher::SketchObject& exactSketch) {
                Gui::cmdAppObjectArgs(
                    &exactSketch,
                    "delConstraintsToExternal()"
                );
            },
            true
        )) {
        return;
    }
    auto* exactSketch = resolveExactSketch();
    if (!exactSketch) {
        return;
    }

    Gui::TranslatedNotification(
        exactSketch,
        tr("Delete constraints to external geom."),
        tr("All constraints that deal with external geometry were deleted.")
    );
}

void SketcherValidation::showPoints(const std::vector<Base::Vector3d>& pts)
{
    hidePoints();

    auto* exactSketch = resolveExactSketch();
    if (!exactSketch || !Gui::Application::Instance) {
        return;
    }

    Gui::ViewProvider* vp =
        Gui::Application::Instance->getViewProvider(exactSketch);
    if (!vp || !vp->getRoot()) {
        return;
    }

    auto coords = new SoCoordinate3();
    auto drawStyle = new SoDrawStyle();
    drawStyle->pointSize = 6;
    auto pcPoints = new SoPointSet();

    coincidenceRoot = new SoGroup();
    // Keep the marker group alive independently of the view provider. A document
    // may close while the validation panel is still being torn down.
    coincidenceRoot->ref();

    coincidenceRoot->addChild(drawStyle);
    auto pointsep = new SoSeparator();
    auto basecol = new SoBaseColor();
    basecol->rgb.setValue(1.0F, 0.5F, 0.0F);
    pointsep->addChild(basecol);
    pointsep->addChild(coords);
    pointsep->addChild(pcPoints);
    coincidenceRoot->addChild(pointsep);

    // Draw markers
    auto markcol = new SoBaseColor();
    markcol->rgb.setValue(1.0F, 1.0F, 0.0F);
    auto marker = new SoMarkerSet();
    long markerSize = App::GetApplication()
                          .GetParameterGroupByPath("User parameter:BaseApp/Preferences/View")
                          ->GetInt("MarkerSize", 9);
    marker->markerIndex = Gui::Inventor::MarkerBitmaps::getMarkerIndex("PLUS", int(markerSize));
    pointsep->addChild(markcol);
    pointsep->addChild(marker);

    int pts_size = (int)pts.size();
    coords->point.setNum(pts_size);
    SbVec3f* c = coords->point.startEditing();
    for (int i = 0; i < pts_size; i++) {
        const Base::Vector3d& v = pts[i];
        c[i].setValue((float)v.x, (float)v.y, (float)v.z);
    }
    coords->point.finishEditing();

    vp->getRoot()->addChild(coincidenceRoot);
}

void SketcherValidation::hidePoints()
{
    if (coincidenceRoot) {
        auto* exactSketch = resolveExactSketch(false);
        if (exactSketch && Gui::Application::Instance) {
            Gui::ViewProvider* vp =
                Gui::Application::Instance->getViewProvider(
                    exactSketch
                );
            if (vp && vp->getRoot() && vp->getRoot()->findChild(coincidenceRoot) >= 0) {
                vp->getRoot()->removeChild(coincidenceRoot);
            }
        }
        coincidenceRoot->unref();
        coincidenceRoot = nullptr;
    }
}

void SketcherValidation::onFindDegeneratedClicked()
{
    auto* exactSketch = resolveExactSketch();
    if (!exactSketch) {
        return;
    }

    double prec = Precision::Confusion();
    int count = exactSketch->detectDegeneratedGeometries(prec);

    if (count == 0) {
        Gui::TranslatedNotification(
            exactSketch,
            tr("No degenerated geometry"),
            tr("No degenerated geometry found")
        );

        ui->fixDegenerated->setEnabled(false);
    }
    else {
        Gui::TranslatedUserWarning(
            exactSketch,
            tr("Degenerated geometry"),
            tr("%1 degenerated geometry found").arg(count)
        );

        ui->fixDegenerated->setEnabled(true);
    }
}

void SketcherValidation::onFixDegeneratedClicked()
{
    if (!resolveExactSketch()) {
        return;
    }

    const double precision = Precision::Confusion();
    Gui::WaitCursor wait;
    if (!runExactMutation(
            "Remove degenerated geometry",
            [precision](Sketcher::SketchObject& exactSketch) {
                Gui::cmdAppObjectArgs(
                    &exactSketch,
                    "removeDegeneratedGeometries(%.12f)",
                    precision
                );
            },
            true
        )) {
        return;
    }

    ui->fixButton->setEnabled(false);
    hidePoints();
}

// -----------------------------------------------

TaskSketcherValidation::TaskSketcherValidation(Sketcher::SketchObject* Obj)
{
    setAutoCloseOnDeletedDocument(true);

    QWidget* widget = new SketcherValidation(Obj);
    auto taskbox = new Gui::TaskView::TaskBox(QPixmap(), widget->windowTitle(), true, nullptr);
    taskbox->groupLayout()->addWidget(widget);
    Content.push_back(taskbox);
}

TaskSketcherValidation::~TaskSketcherValidation() = default;

#include "moc_TaskSketcherValidation.cpp"
