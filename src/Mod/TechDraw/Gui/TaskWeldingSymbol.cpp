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
# include <QMessageBox>
# include <QPushButton>

#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <Base/Console.h>
#include <Base/Tools.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/Document.h>

#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawLeaderLine.h>
#include <Mod/TechDraw/App/DrawTileWeld.h>
#include <Mod/TechDraw/App/DrawWeldSymbol.h>

#include "TaskWeldingSymbol.h"
#include "ui_TaskWeldingSymbol.h"
#include "PreferencesGui.h"
#include "SymbolChooser.h"


using namespace Gui;
using namespace TechDraw;
using namespace TechDrawGui;

//ctor for creation
TaskWeldingSymbol::TaskWeldingSymbol(TechDraw::DrawLeaderLine* leadFeat) :
    ui(new Ui_TaskWeldingSymbol),
    m_leadFeat(leadFeat),
    m_weldFeat(nullptr),
    m_arrowFeat(nullptr),
    m_otherFeat(nullptr),
    m_btnOK(nullptr),
    m_btnCancel(nullptr),
    m_createMode(true),
    m_otherDirty(false)
{
    if (!m_leadFeat || !m_leadFeat->findParentPage()
        || m_leadFeat->getDocument()->getBookedTransactionID()
            == App::NullTransaction) {
        throw Base::RuntimeError(
            "A weld symbol requires a leader on a live drawing page and "
            "its owning transaction"
        );
    }
    m_documentIdentity =
        TaskInternal::DocumentIdentity(
            m_leadFeat->getDocument()
        );
    m_leaderIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawLeaderLine>(
            m_leadFeat
        );
    ui->setupUi(this);

    setUiPrimary();

    connect(ui->pbArrowSymbol, &QPushButton::clicked,
            this, &TaskWeldingSymbol::onArrowSymbolCreateClicked);
    connect(ui->pbOtherSymbol, &QPushButton::clicked,
            this, &TaskWeldingSymbol::onOtherSymbolCreateClicked);
    connect(ui->pbOtherErase, &QPushButton::clicked,
            this, &TaskWeldingSymbol::onOtherEraseCreateClicked);
    connect(ui->pbFlipSides, &QPushButton::clicked,
            this, &TaskWeldingSymbol::onFlipSidesCreateClicked);
    connect(ui->fcSymbolDir, &FileChooser::fileNameSelected,
            this, &TaskWeldingSymbol::onDirectorySelected);
}

//ctor for edit
TaskWeldingSymbol::TaskWeldingSymbol(TechDraw::DrawWeldSymbol* weld) :
    ui(new Ui_TaskWeldingSymbol),
    m_leadFeat(nullptr),
    m_weldFeat(weld),
    m_arrowFeat(nullptr),
    m_otherFeat(nullptr),
    m_btnOK(nullptr),
    m_btnCancel(nullptr),
    m_createMode(false),
    m_otherDirty(false)
{
    App::DocumentObject* obj =
        m_weldFeat ? m_weldFeat->Leader.getValue() : nullptr;
    if (!obj ||
        !obj->isDerivedFrom<TechDraw::DrawLeaderLine>() )  {
        throw Base::RuntimeError(
            "The weld symbol has no live leader"
        );
    }

    m_leadFeat = static_cast<TechDraw::DrawLeaderLine*>(obj);
    if (!m_weldFeat->findParentPage()
        || m_leadFeat->getDocument()
            != m_weldFeat->getDocument()
        || m_weldFeat->getDocument()->getBookedTransactionID()
            == App::NullTransaction) {
        throw Base::RuntimeError(
            "The weld editor requires a symbol on a live page and its "
            "owning transaction"
        );
    }
    m_documentIdentity =
        TaskInternal::DocumentIdentity(
            m_weldFeat->getDocument()
        );
    m_leaderIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawLeaderLine>(
            m_leadFeat
        );
    m_weldIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawWeldSymbol>(
            m_weldFeat
        );

    ui->setupUi(this);

    setUiEdit();

    connect(ui->pbArrowSymbol, &QPushButton::clicked,
        this, &TaskWeldingSymbol::onArrowSymbolClicked);
    connect(ui->pbOtherSymbol, &QPushButton::clicked,
        this, &TaskWeldingSymbol::onOtherSymbolClicked);
    connect(ui->pbOtherErase, &QPushButton::clicked,
        this, &TaskWeldingSymbol::onOtherEraseClicked);
    connect(ui->pbFlipSides, &QPushButton::clicked,
        this, &TaskWeldingSymbol::onFlipSidesClicked);

    connect(ui->fcSymbolDir, &FileChooser::fileNameSelected,
        this, &TaskWeldingSymbol::onDirectorySelected);

    connect(ui->leArrowTextL, &QLineEdit::textEdited,
        this, &TaskWeldingSymbol::onArrowTextChanged);
    connect(ui->leArrowTextR, &QLineEdit::textEdited,
        this, &TaskWeldingSymbol::onArrowTextChanged);
    connect(ui->leArrowTextC, &QLineEdit::textEdited,
        this, &TaskWeldingSymbol::onArrowTextChanged);

    connect(ui->leOtherTextL, &QLineEdit::textEdited,
        this, &TaskWeldingSymbol::onOtherTextChanged);
    connect(ui->leOtherTextR, &QLineEdit::textEdited,
        this, &TaskWeldingSymbol::onOtherTextChanged);
    connect(ui->leOtherTextC, &QLineEdit::textEdited,
        this, &TaskWeldingSymbol::onOtherTextChanged);

    connect(ui->leTailText, &QLineEdit::textEdited,
        this, &TaskWeldingSymbol::onWeldingChanged);
    connect(ui->cbFieldWeld, &QCheckBox::toggled,
        this, &TaskWeldingSymbol::onWeldingChanged);
    connect(ui->cbAllAround, &QCheckBox::toggled,
        this, &TaskWeldingSymbol::onWeldingChanged);
    connect(ui->cbAltWeld, &QCheckBox::toggled,
        this, &TaskWeldingSymbol::onWeldingChanged);
}

TaskWeldingSymbol::~TaskWeldingSymbol()
{
}

bool TaskWeldingSymbol::resolveTargets()
{
    auto* document = m_documentIdentity.resolve();
    auto* leader = m_leaderIdentity.resolve();
    if (!document || !leader
        || leader->getDocument() != document
        || !leader->findParentPage()) {
        return false;
    }
    TechDraw::DrawWeldSymbol* weld = nullptr;
    if (m_weldIdentity.id() >= 0) {
        weld = m_weldIdentity.resolve();
        if (!weld || weld->getDocument() != document
            || weld->Leader.getValue() != leader) {
            return false;
        }
    }
    m_leadFeat = leader;
    m_weldFeat = weld;
    return true;
}

void TaskWeldingSymbol::updateTask()
{
//    blockUpdate = true;

//    blockUpdate = false;
}

void TaskWeldingSymbol::changeEvent(QEvent *event)
{
    if (event->type() == QEvent::LanguageChange) {
        ui->retranslateUi(this);
    }
}

void TaskWeldingSymbol::setUiPrimary()
{
//    Base::Console().message("TWS::setUiPrimary()\n");
    setWindowTitle(QObject::tr("Create Welding Symbol"));
    m_currDir = PreferencesGui::weldingDirectory();
    ui->fcSymbolDir->setFileName(m_currDir);

    ui->pbArrowSymbol->setFocus();
    m_arrowOut.init();
    m_arrowPath = QString();
    m_arrowSymbol = QString();
    m_otherOut.init();
    m_otherPath = QString();
    m_otherSymbol = QString();

    // we must mark the other side dirty to assure it gets created
    m_otherDirty = true;
}

void TaskWeldingSymbol::setUiEdit()
{
//    Base::Console().message("TWS::setUiEdit()\n");
    setWindowTitle(QObject::tr("Edit Welding Symbol"));

    m_currDir = PreferencesGui::weldingDirectory();
    ui->fcSymbolDir->setFileName(m_currDir);

    ui->cbAllAround->setChecked(m_weldFeat->AllAround.getValue());
    ui->cbFieldWeld->setChecked(m_weldFeat->FieldWeld.getValue());
    ui->cbAltWeld->setChecked(m_weldFeat->AlternatingWeld.getValue());
    ui->leTailText->setText(QString::fromUtf8(m_weldFeat->TailText.getValue()));

    getTileFeats();
    if (m_arrowFeat) {
        QString qTemp = QString::fromUtf8(m_arrowFeat->LeftText.getValue());
        ui->leArrowTextL->setText(qTemp);
        qTemp = QString::fromUtf8(m_arrowFeat->RightText.getValue());
        ui->leArrowTextR->setText(qTemp);
        qTemp = QString::fromUtf8(m_arrowFeat->CenterText.getValue());
        ui->leArrowTextC->setText(qTemp);

        std::string inFile = m_arrowFeat->SymbolFile.getValue();
        auto fi = Base::FileInfo(inFile);
        if (fi.isReadable()) {
            qTemp = QString::fromUtf8(m_arrowFeat->SymbolFile.getValue());
            QIcon targetIcon(qTemp);
            QSize iconSize(32, 32);
            ui->pbArrowSymbol->setIcon(targetIcon);
            ui->pbArrowSymbol->setIconSize(iconSize);
            ui->pbArrowSymbol->setText(QString());
        } else {
            ui->pbArrowSymbol->setText(tr("Symbol"));
        }
    }

    if (m_otherFeat) {
        QString qTemp = QString::fromUtf8(m_otherFeat->LeftText.getValue());
        ui->leOtherTextL->setText(qTemp);
        qTemp = QString::fromUtf8(m_otherFeat->RightText.getValue());
        ui->leOtherTextR->setText(qTemp);
        qTemp = QString::fromUtf8(m_otherFeat->CenterText.getValue());
        ui->leOtherTextC->setText(qTemp);

        std::string inFile = m_otherFeat->SymbolFile.getValue();
        auto fi = Base::FileInfo(inFile);
        if (fi.isReadable()) {
            qTemp = QString::fromUtf8(m_otherFeat->SymbolFile.getValue());
            QIcon targetIcon(qTemp);
            QSize iconSize(32, 32);
            ui->pbOtherSymbol->setIcon(targetIcon);
            ui->pbOtherSymbol->setIconSize(iconSize);
            ui->pbOtherSymbol->setText(QString());
        } else {
            ui->pbOtherSymbol->setText(tr("Symbol"));
        }
    }

    ui->pbArrowSymbol->setFocus();
}

void TaskWeldingSymbol::symbolDialog(const char* source)
{
    QString _source = tr(source);
    SymbolChooser* dlg = new SymbolChooser(this, m_currDir, _source);
    connect(dlg, &SymbolChooser::symbolSelected,
            this, &TaskWeldingSymbol::onSymbolSelected);
    dlg->setAttribute(Qt::WA_DeleteOnClose);
    dlg->exec();
}

void TaskWeldingSymbol::onArrowSymbolCreateClicked()
{
    symbolDialog("arrow");
}

void TaskWeldingSymbol::onArrowSymbolClicked()
{
    symbolDialog("arrow");
    updateTiles();
    m_weldFeat->requestPaint();
}

void TaskWeldingSymbol::onOtherSymbolCreateClicked()
{
    symbolDialog("other");
}

void TaskWeldingSymbol::onOtherSymbolClicked()
{
    symbolDialog("other");
    updateTiles();
    m_weldFeat->requestPaint();
}

void TaskWeldingSymbol::onOtherEraseCreateClicked()
{
    ui->leOtherTextL->setText(QString());
    ui->leOtherTextC->setText(QString());
    ui->leOtherTextR->setText(QString());
    ui->pbOtherSymbol->setIcon(QIcon());
    ui->pbOtherSymbol->setText(tr("Symbol"));
    m_otherOut.init();
    m_otherPath = QString();
}

void TaskWeldingSymbol::onOtherEraseClicked()
{
    m_otherDirty = true;
    ui->leOtherTextL->setText(QString());
    ui->leOtherTextC->setText(QString());
    ui->leOtherTextR->setText(QString());
    ui->pbOtherSymbol->setIcon(QIcon());
    ui->pbOtherSymbol->setText(tr("Symbol"));
    m_otherOut.init();
    m_otherPath = QString();
    updateTiles();
    m_weldFeat->requestPaint();
}

void TaskWeldingSymbol::onFlipSidesCreateClicked()
{
    QString tempText = ui->leOtherTextL->text();
    ui->leOtherTextL->setText(ui->leArrowTextL->text());
    ui->leArrowTextL->setText(tempText);
    tempText = ui->leOtherTextC->text();
    ui->leOtherTextC->setText(ui->leArrowTextC->text());
    ui->leArrowTextC->setText(tempText);
    tempText = ui->leOtherTextR->text();
    ui->leOtherTextR->setText(ui->leArrowTextR->text());
    ui->leArrowTextR->setText(tempText);

    QString tempPathArrow = m_otherPath;
    m_otherPath = m_arrowPath;
    m_arrowPath = tempPathArrow;
    tempText = ui->pbOtherSymbol->text();
    ui->pbOtherSymbol->setText(ui->pbArrowSymbol->text());
    ui->pbArrowSymbol->setText(tempText);
    QIcon tempIcon = ui->pbOtherSymbol->icon();
    ui->pbOtherSymbol->setIcon(ui->pbArrowSymbol->icon());
    ui->pbArrowSymbol->setIcon(tempIcon);
}

void TaskWeldingSymbol::onFlipSidesClicked()
{
    QString tempText = ui->leOtherTextL->text();
    ui->leOtherTextL->setText(ui->leArrowTextL->text());
    ui->leArrowTextL->setText(tempText);
    tempText = ui->leOtherTextC->text();
    ui->leOtherTextC->setText(ui->leArrowTextC->text());
    ui->leArrowTextC->setText(tempText);
    tempText = ui->leOtherTextR->text();
    ui->leOtherTextR->setText(ui->leArrowTextR->text());
    ui->leArrowTextR->setText(tempText);

    // one cannot get the path from the icon therefore read out
    // the path property
    auto tempPathArrow = m_arrowFeat->SymbolFile.getValue();
    auto tempPathOther = m_otherFeat->SymbolFile.getValue();
    m_otherPath = QString::fromLatin1(tempPathArrow);
    m_arrowPath = QString::fromLatin1(tempPathOther);
    QIcon tempIcon = ui->pbOtherSymbol->icon();
    ui->pbOtherSymbol->setIcon(ui->pbArrowSymbol->icon());
    ui->pbArrowSymbol->setIcon(tempIcon);

    m_otherDirty = true;
    updateTiles();
    m_weldFeat->requestPaint();
}

void TaskWeldingSymbol::onArrowTextChanged()
{
    updateTiles();
    m_weldFeat->requestPaint();
}

void TaskWeldingSymbol::onOtherTextChanged()
{
    m_otherDirty = true;
    updateTiles();
    m_weldFeat->requestPaint();
}

void TaskWeldingSymbol::onWeldingChanged()
{
    updateWeldingSymbol();
    m_weldFeat->requestPaint();
}

void TaskWeldingSymbol::onDirectorySelected(const QString& newDir)
{
//    Base::Console().message("TWS::onDirectorySelected(%s)\n", qPrintable(newDir));
    m_currDir = newDir + QStringLiteral("/");
}

void TaskWeldingSymbol::onSymbolSelected(QString symbolPath,
                                         QString source)
{
//    Base::Console().message("TWS::onSymbolSelected(%s) - source: %s\n",
//                            qPrintable(symbolPath), qPrintable(source));
    QIcon targetIcon(symbolPath);
    QSize iconSize(32, 32);
    QString arrow = tr("arrow");
    QString other = tr("other");
    if (source == arrow) {
        ui->pbArrowSymbol->setIcon(targetIcon);
        ui->pbArrowSymbol->setIconSize(iconSize);
        ui->pbArrowSymbol->setText(QString());
        m_arrowPath = symbolPath;
    } else if (source == other) {
        m_otherDirty = true;
        ui->pbOtherSymbol->setIcon(targetIcon);
        ui->pbOtherSymbol->setIconSize(iconSize);
        ui->pbOtherSymbol->setText(QString());
        m_otherPath = symbolPath;
    }
}

void TaskWeldingSymbol::collectArrowData()
{
//    Base::Console().message("TWS::collectArrowData()\n");
    m_arrowOut.toBeSaved = true;
    m_arrowOut.arrowSide = false;
    m_arrowOut.row = 0;
    m_arrowOut.col = 0;
    m_arrowOut.leftText = ui->leArrowTextL->text().toStdString();
    m_arrowOut.centerText = ui->leArrowTextC->text().toStdString();
    m_arrowOut.rightText = ui->leArrowTextR->text().toStdString();
    m_arrowOut.symbolPath= m_arrowPath.toStdString();
    m_arrowOut.tileName = "";
}

void TaskWeldingSymbol::collectOtherData()
{
//    Base::Console().message("TWS::collectOtherData()\n");
    m_otherOut.toBeSaved = true;
    m_otherOut.arrowSide = false;
    m_otherOut.row = -1;
    m_otherOut.col = 0;
    m_otherOut.leftText = ui->leOtherTextL->text().toStdString();
    m_otherOut.centerText = ui->leOtherTextC->text().toStdString();
    m_otherOut.rightText = ui->leOtherTextR->text().toStdString();
    m_otherOut.symbolPath = m_otherPath.toStdString();
    m_otherOut.tileName = "";
}

void TaskWeldingSymbol::getTileFeats()
{
//    Base::Console().message("TWS::getTileFeats()\n");
    std::vector<TechDraw::DrawTileWeld*> tiles = m_weldFeat->getTiles();
    m_arrowFeat = nullptr;
    m_otherFeat = nullptr;

    if (tiles.empty()) {
        return;
    }

    TechDraw::DrawTileWeld* tempTile = tiles.at(0);
    if (tempTile->TileRow.getValue() == 0) {
        m_arrowFeat = tempTile;
    } else {
        m_otherFeat = tempTile;
    }
    if (tiles.size() > 1) {
        TechDraw::DrawTileWeld* tempTile = tiles.at(1);
        if (tempTile->TileRow.getValue() == 0) {
            m_arrowFeat = tempTile;
        } else {
            m_otherFeat = tempTile;
        }
    }
}

//******************************************************************************
TechDraw::DrawWeldSymbol* TaskWeldingSymbol::createWeldingSymbol()
{
    if (!resolveTargets()) {
        throw Base::RuntimeError(
            "The weld-symbol target is no longer available"
        );
    }
    App::Document* doc = m_documentIdentity.resolve();
    auto weldSymbol = doc->addObject<TechDraw::DrawWeldSymbol>("WeldSymbol");
    if (!weldSymbol) {
        throw Base::RuntimeError("TaskWeldingSymbol - new symbol object not found");
    }

    weldSymbol->AllAround.setValue(ui->cbAllAround->isChecked());
    weldSymbol->FieldWeld.setValue(ui->cbFieldWeld->isChecked());
    weldSymbol->AlternatingWeld.setValue(ui->cbAltWeld->isChecked());
    weldSymbol->TailText.setValue(ui->leTailText->text().toStdString());
    weldSymbol->Leader.setValue(m_leadFeat);

    TechDraw::DrawPage *page = m_leadFeat->findParentPage();
    if (page) {
        page->addView(weldSymbol);
    }
    else {
        throw Base::RuntimeError(
            "The weld-symbol leader is not on a drawing page"
        );
    }
    m_weldIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawWeldSymbol>(
            weldSymbol
        );

    return weldSymbol;
}

void TaskWeldingSymbol::updateWeldingSymbol()
{
    m_weldFeat->AllAround.setValue(ui->cbAllAround->isChecked());
    m_weldFeat->FieldWeld.setValue(ui->cbFieldWeld->isChecked());
    m_weldFeat->AlternatingWeld.setValue(ui->cbAltWeld->isChecked());
    m_weldFeat->TailText.setValue(ui->leTailText->text().toStdString());
}

void TaskWeldingSymbol::updateTiles()
{
//    Base::Console().message("TWS::updateTiles()\n");
    getTileFeats();

    if (!m_arrowFeat) {
        Base::Console().message("TWS::updateTiles - no arrow tile!\n");
    } else {
        collectArrowData();
        if (m_arrowOut.toBeSaved) {
            m_arrowFeat->TileColumn.setValue(m_arrowOut.col);
            m_arrowFeat->LeftText.setValue(m_arrowOut.leftText);
            m_arrowFeat->RightText.setValue(m_arrowOut.rightText);
            m_arrowFeat->CenterText.setValue(
                m_arrowOut.centerText
            );
            if (!m_arrowOut.symbolPath.empty()) {
//                m_arrowFeat->replaceSymbol(m_arrowOut.symbolPath);
                m_arrowFeat->SymbolFile.setValue(m_arrowOut.symbolPath);
            }
        }
    }

    if (!m_otherFeat) {
//        Base::Console().message("TWS::updateTiles - no other tile!\n");
    } else {
        if (m_otherDirty) {
            collectOtherData();
            if (m_otherOut.toBeSaved) {
                m_otherFeat->TileColumn.setValue(m_otherOut.col);
                m_otherFeat->LeftText.setValue(
                    m_otherOut.leftText
                );
                m_otherFeat->RightText.setValue(
                    m_otherOut.rightText
                );
                m_otherFeat->CenterText.setValue(
                    m_otherOut.centerText
                );
//                m_otherFeat->replaceSymbol(m_otherOut.symbolPath);
                m_otherFeat->SymbolFile.setValue(m_otherOut.symbolPath);
            }
        }
    }
    return;
}

void TaskWeldingSymbol::saveButtons(QPushButton* btnOK,
                             QPushButton* btnCancel)
{
    m_btnOK = btnOK;
    m_btnCancel = btnCancel;
}

void TaskWeldingSymbol::enableTaskButtons(bool enable)
{
    m_btnOK->setEnabled(enable);
    m_btnCancel->setEnabled(enable);
}

//******************************************************************************

bool TaskWeldingSymbol::accept()
{
    auto* document = m_documentIdentity.resolve();
    if (!document || !resolveTargets()
        || document->getBookedTransactionID()
            == App::NullTransaction) {
        return false;
    }
    try {
        if (m_createMode) {
            if (!m_weldFeat) {
                m_weldFeat = createWeldingSymbol();
            }
            else {
                updateWeldingSymbol();
            }
            updateTiles();
        }
        else {
            updateWeldingSymbol();
            updateTiles();
        }
        if (!m_weldFeat) {
            throw Base::RuntimeError(
                "The weld symbol could not be created"
            );
        }
        m_weldFeat->recomputeFeature();
        if (m_weldFeat->isError()) {
            throw Base::RuntimeError(
                "The weld symbol could not produce a valid result"
            );
        }
        if (m_createMode) {
            auto* timeline =
                App::DocumentTimeline::get(document);
            if (!timeline) {
                throw Base::RuntimeError(
                    "The weld symbol could not access document history"
                );
            }
            std::vector<App::DocumentObject*> timelineBlock;
            timelineBlock.reserve(3);
            for (auto* tile : {m_arrowFeat, m_otherFeat}) {
                if (!tile || tile->getDocument() != document
                    || !document->containsObject(tile)
                    || App::DocumentTimeline::timelineOwner(tile)
                        != m_weldFeat
                    || !timeline
                            ->isProvisionallyEnrolledByCurrentTransaction(
                                tile
                            )) {
                    throw Base::RuntimeError(
                        "The weld symbol did not retain its exact generated "
                        "tiles"
                    );
                }
                timelineBlock.push_back(tile);
            }
            timelineBlock.push_back(m_weldFeat);
            timeline->finalizeProvisionalOperationBlock(
                m_weldFeat,
                timelineBlock
            );
        }
    }
    catch (const Base::Exception& error) {
        QMessageBox::critical(
            this,
            tr("Weld Symbol Failed"),
            QString::fromUtf8(error.what())
        );
        return false;
    }
    TaskInternal::updateExactDocument(document);
    TaskInternal::resetExactEdit(document);

    return true;
}

bool TaskWeldingSymbol::reject()
{
    // TaskView rolls creation/editing back through the exact transaction.
    TaskInternal::resetExactEdit(
        m_documentIdentity.resolve()
    );
    return true;
}
/////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
TaskDlgWeldingSymbol::TaskDlgWeldingSymbol(TechDraw::DrawLeaderLine* leader)
    : TaskDialog()
{
    widget  = new TaskWeldingSymbol(leader);
    taskbox = new Gui::TaskView::TaskBox(Gui::BitmapFactory().pixmap("actions/TechDraw_WeldSymbol"),
                                             widget->windowTitle(), true, nullptr);
    taskbox->groupLayout()->addWidget(widget);
    Content.push_back(taskbox);
    setAutoCloseOnTransactionChange(true);
}

TaskDlgWeldingSymbol::TaskDlgWeldingSymbol(TechDraw::DrawWeldSymbol* weld)
    : TaskDialog()
{
    widget  = new TaskWeldingSymbol(weld);
    taskbox = new Gui::TaskView::TaskBox(Gui::BitmapFactory().pixmap("actions/TechDraw_WeldSymbol"),
                                             widget->windowTitle(), true, nullptr);
    taskbox->groupLayout()->addWidget(widget);
    Content.push_back(taskbox);
    setAutoCloseOnTransactionChange(true);
}

TaskDlgWeldingSymbol::~TaskDlgWeldingSymbol()
{
}

void TaskDlgWeldingSymbol::update()
{
//    widget->updateTask();
}

void TaskDlgWeldingSymbol::modifyStandardButtons(QDialogButtonBox* box)
{
    QPushButton* btnOK = box->button(QDialogButtonBox::Ok);
    QPushButton* btnCancel = box->button(QDialogButtonBox::Cancel);
    widget->saveButtons(btnOK, btnCancel);
}

//==== calls from the TaskView ===============================================================
void TaskDlgWeldingSymbol::open()
{
}

void TaskDlgWeldingSymbol::clicked(int)
{
}

bool TaskDlgWeldingSymbol::accept()
{
    return widget->accept();
}

bool TaskDlgWeldingSymbol::reject()
{
    return widget->reject();
}

#include <Mod/TechDraw/Gui/moc_TaskWeldingSymbol.cpp>
