/***************************************************************************
 *   Copyright (c) 2018 WandererFan <wandererfan@gmail.com>                *
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

#include <Base/Console.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/Selection/Selection.h>
#include <Gui/ViewProvider.h>
#include <Mod/TechDraw/App/DrawUtil.h>
#include <Mod/TechDraw/App/DrawViewPart.h>
#include <Mod/TechDraw/App/CenterLine.h>
#include <Mod/TechDraw/App/Geometry.h>
#include <Mod/TechDraw/App/LineGenerator.h>


#include "TaskLineDecor.h"
#include "ui_TaskLineDecor.h"
#include "ui_TaskRestoreLines.h"
#include "QGIView.h"
#include "ViewProviderViewPart.h"
#include "DrawGuiUtil.h"
#include "LineAttributeBuilder.h"


using namespace Gui;
using namespace TechDraw;
using namespace TechDrawGui;

TaskLineDecor::TaskLineDecor(TechDraw::DrawViewPart* partFeat,
                             std::vector<std::string> edgeNames) :
    ui(new Ui_TaskLineDecor),
    m_partFeat(partFeat),
    m_partIdentity(partFeat),
    m_edges(edgeNames),
    m_apply(true)
{
    if (!partFeat
        || partFeat->getDocument()->getBookedTransactionID()
            == App::NullTransaction) {
        throw Base::RuntimeError(
            "The line appearance editor requires a live drawing "
            "transaction"
        );
    }
    // Line formats are mutable values stored behind pointer properties.
    // Capture all three pre-preview lists before the panel creates or edits
    // an entry, so TaskView Cancel can restore the exact prior state.
    partFeat->GeomFormats.setValues(partFeat->GeomFormats.getValues());
    partFeat->CosmeticEdges.setValues(
        partFeat->CosmeticEdges.getValues()
    );
    partFeat->CenterLines.setValues(partFeat->CenterLines.getValues());

    initializeRejectArrays();
    m_lineGenerator = new TechDraw::LineGenerator;

    ui->setupUi(this);

    getDefaults();
    initUi();

    connect(ui->cb_Style, qOverload<int>(&QComboBox::currentIndexChanged), this, &TaskLineDecor::onStyleChanged);
    connect(ui->cc_Color, &ColorButton::changed, this, &TaskLineDecor::onColorChanged);
    connect(ui->dsb_Weight, qOverload<double>(&QuantitySpinBox::valueChanged), this, &TaskLineDecor::onWeightChanged);
    connect(ui->cb_Visible, &QCheckBox::toggled, this, &TaskLineDecor::onVisibleChanged);
}

TaskLineDecor::~TaskLineDecor()
{
    delete m_lineGenerator;
}

void TaskLineDecor::initUi()
{
    std::string viewName = m_partFeat->getNameInDocument();
    ui->le_View->setText(QString::fromStdString(viewName));

    ui->le_Lines->setText(tr("%n line(s)", "", static_cast<int>(m_edges.size())));

    ui->cc_Color->setColor(m_color.asValue<QColor>());
    ui->dsb_Weight->setValue(m_weight);
    ui->dsb_Weight->setSingleStep(0.1);
    ui->cb_Visible->setChecked(m_visible);

    // line numbering starts at 1, not 0
    DrawGuiUtil::loadLineStyleChoices(ui->cb_Style, m_lineGenerator);
    if (ui->cb_Style->count() >= m_lineNumber ) {
        ui->cb_Style->setCurrentIndex(m_lineNumber - 1);
    }
}

TechDraw::LineFormat *TaskLineDecor::getFormatAccessPtr(const std::string &edgeName, std::string *newFormatTag)
{
    return drawingLineFormatFromSelection(
        m_partFeat,
        edgeName,
        true,
        newFormatTag);
}

void TaskLineDecor::initializeRejectArrays()
{
    m_originalFormats.resize(m_edges.size());
    m_createdFormatTags.resize(m_edges.size());

    for (size_t i = 0; i < m_edges.size(); ++i) {
        std::string newTag;
        TechDraw::LineFormat *accessPtr = getFormatAccessPtr(m_edges[i], &newTag);

        if (accessPtr) {
            m_originalFormats[i] = *accessPtr;
            if (!newTag.empty()) {
                m_createdFormatTags[i] = newTag;
            }
        }
    }
}

// get the current line tool appearance default
void TaskLineDecor::getDefaults()
{
//    Base::Console().message("TLD::getDefaults()\n");
    m_color = LineFormat::getCurrentLineFormat().getColor();
    m_weight = LineFormat::getCurrentLineFormat().getWidth();
    m_visible = LineFormat::getCurrentLineFormat().getVisible();
    m_lineNumber = LineFormat::getCurrentLineFormat().getLineNumber();

    //set defaults to format of 1st edge
    if (!m_originalFormats.empty()) {
        LineFormat &lf = m_originalFormats.front();
        m_style = lf.getStyle();
        m_color = lf.getColor();
        m_weight = lf.getWidth();
        m_visible = lf.getVisible();
        m_lineNumber = lf.getLineNumber();
    }
}

void TaskLineDecor::onStyleChanged()
{
    m_lineNumber = ui->cb_Style->currentIndex() + 1;
    applyDecorations();
    m_partFeat->requestPaint();
}

void TaskLineDecor::onColorChanged()
{
    m_color.setValue<QColor>(ui->cc_Color->color());
    applyDecorations();
    m_partFeat->requestPaint();
}

void TaskLineDecor::onWeightChanged()
{
    m_weight = ui->dsb_Weight->value().getValue();
    applyDecorations();
    m_partFeat->requestPaint();
}

void TaskLineDecor::onVisibleChanged(bool checked)
{
    m_visible = checked;
    applyDecorations();
    m_partFeat->requestPaint();
}

void TaskLineDecor::applyDecorations()
{
    LineFormat format(m_style, m_weight, m_color, m_visible);
    format.setLineNumber(m_lineNumber);
    changeDrawingLineAttributes(
        m_partFeat,
        drawingLineTargetsFromSelection(m_partFeat, m_edges),
        format);
}

bool TaskLineDecor::accept()
{
    auto* partFeature = m_partIdentity.resolve();
    if (!partFeature) {
        return false;
    }

    if (apply()) {
        applyDecorations();
    }

    partFeature->requestPaint();
    TaskInternal::updateExactDocument(partFeature->getDocument());
    TaskInternal::resetExactEdit(partFeature->getDocument());

    return true;
}

bool TaskLineDecor::reject()
{
    auto* partFeature = m_partIdentity.resolve();
    if (!partFeature) {
        return false;
    }

    // TaskView owns the transaction opened by the ribbon command.  Its
    // rollback removes provisional GeomFormats and restores all nested line
    // values from the deep property snapshots.
    TaskInternal::resetExactEdit(partFeature->getDocument());
    return true;
}

void TaskLineDecor::changeEvent(QEvent *e)
{
    if (e->type() == QEvent::LanguageChange) {
        ui->retranslateUi(this);
    }
}
//////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
TaskRestoreLines::TaskRestoreLines(TechDraw::DrawViewPart* partFeat,
                                   TaskLineDecor* parent) :
    ui(new Ui_TaskRestoreLines),
    m_partFeat(partFeat),
    m_parent(parent)
{
    ui->setupUi(this);

    connect(ui->pb_All, &QPushButton::clicked, this, &TaskRestoreLines::onAllPressed);
    connect(ui->pb_Geometry, &QPushButton::clicked, this, &TaskRestoreLines::onGeometryPressed);
    connect(ui->pb_Cosmetic, &QPushButton::clicked, this, &TaskRestoreLines::onCosmeticPressed);
    connect(ui->pb_Center, &QPushButton::clicked, this, &TaskRestoreLines::onCenterPressed);

    initUi();
}

TaskRestoreLines::~TaskRestoreLines()
{
}

void TaskRestoreLines::initUi()
{
    ui->l_All->setText(QString::number(countInvisibleLines()));
    ui->l_Geometry->setText(QString::number(countInvisibleGeoms()));
    ui->l_Cosmetic->setText(QString::number(countInvisibleCosmetics()));
    ui->l_Center->setText(QString::number(countInvisibleCenters()));
}

void TaskRestoreLines::onAllPressed()
{
//    Base::Console().message("TRL::onAllPressed()\n");
    onGeometryPressed();
    onCosmeticPressed();
    onCenterPressed();
}

void TaskRestoreLines::onGeometryPressed()
{
//    Base::Console().message("TRL::onGeometryPressed()\n");
    restoreInvisibleGeoms();
    ui->l_Geometry->setText(QString::number(0));
    ui->l_All->setText(QString::number(countInvisibleLines()));
}

void TaskRestoreLines::onCosmeticPressed()
{
//    Base::Console().message("TRL::onCosmeticPressed()\n");
    restoreInvisibleCosmetics();
    ui->l_Cosmetic->setText(QString::number(0));
    ui->l_All->setText(QString::number(countInvisibleLines()));
}

void TaskRestoreLines::onCenterPressed()
{
//    Base::Console().message("TRL::onCenterPressed()\n");
    restoreInvisibleCenters();
    ui->l_Center->setText(QString::number(0));
    ui->l_All->setText(QString::number(countInvisibleLines()));
}

int TaskRestoreLines::countInvisibleLines()
{
    int result = 0;
    result += countInvisibleGeoms();
    result += countInvisibleCosmetics();
    result += countInvisibleCenters();
    return result;
}

int TaskRestoreLines::countInvisibleGeoms()
{
    int iGeoms = 0;
    const std::vector<TechDraw::GeomFormat*> geoms = m_partFeat->GeomFormats.getValues();
    for (auto& g : geoms) {
        if (!g->m_format.getVisible()) {
            iGeoms++;
        }
    }
    return iGeoms;
}

int TaskRestoreLines::countInvisibleCosmetics()
{
    int iCosmos = 0;
    const std::vector<TechDraw::CosmeticEdge*> cosmos = m_partFeat->CosmeticEdges.getValues();
    for (auto& c : cosmos) {
        if (!c->m_format.getVisible()) {
            iCosmos++;
        }
    }
    return iCosmos;
}

int TaskRestoreLines::countInvisibleCenters()
{
    int iCenter = 0;
    const std::vector<TechDraw::CenterLine*> centers = m_partFeat->CenterLines.getValues();
    for (auto& c : centers) {
        if (!c->m_format.getVisible()) {
            iCenter++;
        }
    }
    return iCenter;
}

void TaskRestoreLines::restoreInvisibleLines()
{
    restoreInvisibleGeoms();
    restoreInvisibleCosmetics();
    restoreInvisibleCenters();
}

void TaskRestoreLines::restoreInvisibleGeoms()
{
    const std::vector<TechDraw::GeomFormat*> geoms = m_partFeat->GeomFormats.getValues();
    for (auto& g : geoms) {
        if (!g->m_format.getVisible()) {
            g->m_format.setVisible(true);
        }
    }
    m_partFeat->GeomFormats.setValues(geoms);
    m_parent->apply(false);                   //don't undo the work we just did
}

void TaskRestoreLines::restoreInvisibleCosmetics()
{
    const std::vector<TechDraw::CosmeticEdge*> cosmos = m_partFeat->CosmeticEdges.getValues();
    for (auto& c : cosmos) {
        if (!c->m_format.getVisible()) {
            c->m_format.setVisible(true);
        }
    }
    m_partFeat->CosmeticEdges.setValues(cosmos);
    m_parent->apply(false);                   //don't undo the work we just did
}

void TaskRestoreLines::restoreInvisibleCenters()
{
    const std::vector<TechDraw::CenterLine*> centers = m_partFeat->CenterLines.getValues();
    for (auto& c : centers) {
        if (!c->m_format.getVisible()) {
            c->m_format.setVisible(true);
        }
    }
    m_partFeat->CenterLines.setValues(centers);
    m_parent->apply(false);                   //don't undo the work we just did
}


bool TaskRestoreLines::accept()
{
//    Base::Console().message("TRL::accept()\n");
    return true;
}

bool TaskRestoreLines::reject()
{
//    Base::Console().message("TRL::reject()\n");
    return false;
}

void TaskRestoreLines::changeEvent(QEvent *e)
{
    if (e->type() == QEvent::LanguageChange) {
        ui->retranslateUi(this);
    }
}

/////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
TaskDlgLineDecor::TaskDlgLineDecor(TechDraw::DrawViewPart* partFeat,
                                   std::vector<std::string> edgeNames) :
    TaskDialog()
{
    widget  = new TaskLineDecor(partFeat, edgeNames);
    taskbox = new Gui::TaskView::TaskBox(Gui::BitmapFactory().pixmap("actions/TechDraw_DecorateLine"),
                                         widget->windowTitle(), true, nullptr);
    taskbox->groupLayout()->addWidget(widget);
    Content.push_back(taskbox);
    if (edgeNames.empty()) {
        taskbox->hideGroupBox();
    }

    TaskLineDecor* parent = qobject_cast<TaskLineDecor*>(widget);
    if (parent) {
        restore = new TaskRestoreLines(partFeat, parent);
        restoreBox = new Gui::TaskView::TaskBox(Gui::BitmapFactory().pixmap("actions/TechDraw_DecorateLine"),
                                             tr("Restore Invisible Lines"), true, nullptr);
        restoreBox->groupLayout()->addWidget(restore);
        Content.push_back(restoreBox);
    }
    setAutoCloseOnTransactionChange(true);
}

TaskDlgLineDecor::~TaskDlgLineDecor()
{
}

//==== calls from the TaskView ===============================================================
void TaskDlgLineDecor::open()
{

}

void TaskDlgLineDecor::clicked(int i)
{
    Q_UNUSED(i);
}

bool TaskDlgLineDecor::accept()
{
    return widget->accept();
}

bool TaskDlgLineDecor::reject()
{
    return widget->reject();
}

#include <Mod/TechDraw/Gui/moc_TaskLineDecor.cpp>
