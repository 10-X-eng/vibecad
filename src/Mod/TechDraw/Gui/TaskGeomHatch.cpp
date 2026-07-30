/***************************************************************************
 *   Copyright (c) 2017 WandererFan <wandererfan@gmail.com>                *
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

# include <algorithm>
# include <cmath>

#include <App/Document.h>
#include <Base/Console.h>
#include <Base/Vector3D.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/ViewProvider.h>
#include <Mod/TechDraw/App/DrawGeomHatch.h>
#include <Mod/TechDraw/App/DrawView.h>
#include <Mod/TechDraw/App/DrawUtil.h>

#include "TaskGeomHatch.h"
#include "ui_TaskGeomHatch.h"
#include "ViewProviderGeomHatch.h"


using namespace Gui;
using namespace TechDraw;
using namespace TechDrawGui;
using DU = DrawUtil;

TaskGeomHatch::TaskGeomHatch(TechDraw::DrawGeomHatch* inHatch, TechDrawGui::ViewProviderGeomHatch* inVp, bool mode) :
    ui(new Ui_TaskGeomHatch),
    m_hatch(inHatch),
    m_Vp(inVp),
    m_createMode(mode)
{
    if (!inHatch || !inVp
        || inVp->getViewObject() != inHatch
        || inHatch->getDocument()->getBookedTransactionID()
            == App::NullTransaction) {
        throw Base::RuntimeError(
            "The geometric hatch requires a live object, view provider, "
            "and owning transaction"
        );
    }
    ui->setupUi(this);
    connect(ui->fcFile, &FileChooser::fileNameSelected, this, &TaskGeomHatch::onFileChanged);

    m_source = dynamic_cast<TechDraw::DrawView*>(
        m_hatch->Source.getValue()
    );
    if (!m_source
        || m_source->getDocument() != m_hatch->getDocument()) {
        throw Base::RuntimeError(
            "The geometric hatch has no live source view"
        );
    }
    m_hatchIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawGeomHatch>(m_hatch);
    m_sourceIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawView>(m_source);
    getParameters();
    initUi();
}

bool TaskGeomHatch::resolveTargets()
{
    auto* hatch = m_hatchIdentity.resolve();
    auto* source = m_sourceIdentity.resolve();
    if (!hatch || !source
        || hatch->getDocument() != source->getDocument()) {
        return false;
    }
    auto* guiDocument =
        Gui::Application::Instance->getDocument(hatch->getDocument());
    auto* provider = guiDocument
        ? dynamic_cast<ViewProviderGeomHatch*>(
              guiDocument->getViewProvider(hatch)
          )
        : nullptr;
    if (!provider) {
        return false;
    }
    m_hatch = hatch;
    m_source = source;
    m_Vp = provider;
    return true;
}

void TaskGeomHatch::initUi()
{
    ui->fcFile->setFileName(QString::fromUtf8(m_file.data(), m_file.size()));
    std::vector<std::string> names = PATLineSpec::getPatternList(m_file);
    QStringList qsNames = listToQ(names);

    ui->cbName->addItems(qsNames);
    int nameIndex = ui->cbName->findText(QString::fromUtf8(m_name.data(), m_name.size()));
    if (nameIndex > -1) {
        ui->cbName->setCurrentIndex(nameIndex);
    } else {
        Base::Console().warning("Warning - Pattern name *%s* not found in current PAT file\n", m_name.c_str());
    }
    connect(ui->cbName, qOverload<int>(&QComboBox::currentIndexChanged), this, &TaskGeomHatch::onNameChanged);

    ui->sbScale->setValue(m_scale);
    ui->sbScale->setSingleStep(0.1);
    connect(ui->sbScale, qOverload<double>(&QuantitySpinBox::valueChanged), this, &TaskGeomHatch::onScaleChanged);
    ui->sbWeight->setValue(m_weight);
    ui->sbWeight->setSingleStep(0.1);
    connect(ui->sbWeight, qOverload<double>(&QuantitySpinBox::valueChanged), this, &TaskGeomHatch::onLineWeightChanged);
    ui->ccColor->setColor(m_color.asValue<QColor>());
    connect(ui->ccColor, &ColorButton::changed, this, &TaskGeomHatch::onColorChanged);

    ui->dsbRotation->setValue(m_rotation);
    connect(ui->dsbRotation, qOverload<double>(&QDoubleSpinBox::valueChanged), this, &TaskGeomHatch::onRotationChanged);
    ui->dsbOffsetX->setValue(m_offset.x);
    connect(ui->dsbOffsetX, qOverload<double>(&QDoubleSpinBox::valueChanged), this, &TaskGeomHatch::onOffsetChanged);
    ui->dsbOffsetY->setValue(m_offset.y);
    connect(ui->dsbOffsetY, qOverload<double>(&QDoubleSpinBox::valueChanged), this, &TaskGeomHatch::onOffsetChanged);
}

void TaskGeomHatch::onFileChanged()
{
    if (!resolveTargets()) {
        return;
    }
    auto filespec = ui->fcFile->fileName().toStdString();
    m_file = DU::cleanFilespecBackslash(filespec);
    std::vector<std::string> names = PATLineSpec::getPatternList(m_file);
    QStringList qsNames = listToQ(names);
    ui->cbName->clear();
    ui->cbName->addItems(qsNames);
    m_hatch->FilePattern.setValue(m_file);
    onNameChanged();                      //pattern name from old file is not
                                          //necessarily present in new file!
}

void TaskGeomHatch::onNameChanged()
{
    if (!resolveTargets()) {
        return;
    }
    QString cText = ui->cbName->currentText();
    m_name = cText.toUtf8().constData();
    m_hatch->NamePattern.setValue(m_name);
}

void TaskGeomHatch::onScaleChanged()
{
    if (!resolveTargets()) {
        return;
    }
    m_scale = ui->sbScale->value().getValue();
    m_hatch->ScalePattern.setValue(ui->sbScale->value().getValue());
    m_source->requestPaint();
}

void TaskGeomHatch::onRotationChanged()
{
    if (!resolveTargets()) {
        return;
    }
    m_rotation = ui->dsbRotation->value();
    m_hatch->PatternRotation.setValue(m_rotation);
    m_source->requestPaint();
}

void TaskGeomHatch::onOffsetChanged()
{
    if (!resolveTargets()) {
        return;
    }
    Base::Vector3d offset(ui->dsbOffsetX->value(), ui->dsbOffsetY->value(), 0.0);
    m_offset = offset;
    m_hatch->PatternOffset.setValue(m_offset);
    m_source->requestPaint();
}

void TaskGeomHatch::onLineWeightChanged()
{
    if (!resolveTargets()) {
        return;
    }
    m_weight =ui->sbWeight->value().getValue();
    m_Vp->WeightPattern.setValue(ui->sbWeight->value().getValue());
    m_source->requestPaint();
}

void TaskGeomHatch::onColorChanged()
{
    if (!resolveTargets()) {
        return;
    }
    m_color.setValue<QColor>(ui->ccColor->color());
    m_Vp->ColorPattern.setValue(m_color);
}

bool TaskGeomHatch::accept()
{
    if (!resolveTargets()) {
        return false;
    }
    auto patternFile =
        ui->fcFile->fileName().toStdString();
    const auto patternName =
        ui->cbName->currentText().toStdString();
    const auto availablePatterns =
        PATLineSpec::getPatternList(patternFile);
    if (patternName.empty()
        || std::ranges::find(availablePatterns, patternName)
            == availablePatterns.end()) {
        Base::Console().error(
            "Select a valid geometric hatch pattern before accepting.\n"
        );
        return false;
    }
    updateValues();
    m_hatch->recomputeFeature();                     //create the hatch lines
    if (m_hatch->isError()) {
        return false;
    }
    m_source->requestPaint();
    TaskInternal::updateExactDocument(m_hatch->getDocument());
    TaskInternal::resetExactEdit(m_hatch->getDocument());
    return true;
}

bool TaskGeomHatch::reject()
{
    // TaskView owns the exact command/edit transaction. Its Cancel boundary
    // rolls back both provisional creation and live edit changes atomically.
    TaskInternal::resetExactEdit(m_hatchIdentity.resolveDocument());
    return true;
}

void TaskGeomHatch::getParameters()
{
    m_file   = m_hatch->FilePattern.getValue();
    m_name   = m_hatch->NamePattern.getValue();
    m_scale  = m_hatch->ScalePattern.getValue();
    m_rotation = m_hatch->PatternRotation.getValue();
    m_offset   = m_hatch->PatternOffset.getValue();
    m_color  = m_Vp->ColorPattern.getValue();
    m_weight = m_Vp->WeightPattern.getValue();
    if (!getCreateMode()) {
        m_origFile   = m_hatch->FilePattern.getValue();
        m_origName   = m_hatch->NamePattern.getValue();
        m_origScale  = m_hatch->ScalePattern.getValue();
        m_origColor  = m_Vp->ColorPattern.getValue();
        m_origWeight = m_Vp->WeightPattern.getValue();
        m_origRotation = m_hatch->PatternRotation.getValue();
        m_origOffset   = m_hatch->PatternOffset.getValue();
    }
}

//move values from screen to DocObjs
void TaskGeomHatch::updateValues()
{
//    Base::Console().message("TGH::updateValues()\n");
    m_file = (ui->fcFile->fileName()).toUtf8().constData();
    m_hatch->FilePattern.setValue(m_file);
    QString cText = ui->cbName->currentText();
    m_name = cText.toUtf8().constData();
    m_hatch->NamePattern.setValue(m_name);
    m_scale = ui->sbScale->value().getValue();
    m_hatch->ScalePattern.setValue(m_scale);
    m_color.setValue<QColor>(ui->ccColor->color());
    m_Vp->ColorPattern.setValue(m_color);
    m_weight = ui->sbWeight->value().getValue();
    m_Vp->WeightPattern.setValue(m_weight);
    m_hatch->PatternRotation.setValue(ui->dsbRotation->value());
    m_hatch->PatternOffset.setValue(
        Base::Vector3d(
            ui->dsbOffsetX->value(),
            ui->dsbOffsetY->value(),
            0.0
        )
    );
}

QStringList TaskGeomHatch::listToQ(std::vector<std::string> inList)
{
    QStringList result;
    for (auto& s: inList) {
        QString qs = QString::fromUtf8(s.data(), s.size());
        result.append(qs);
    }
    return result;
}

void TaskGeomHatch::changeEvent(QEvent *event)
{
    if (event->type() == QEvent::LanguageChange) {
        ui->retranslateUi(this);
    }
}

/////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
TaskDlgGeomHatch::TaskDlgGeomHatch(TechDraw::DrawGeomHatch* inHatch, TechDrawGui::ViewProviderGeomHatch* inVp, bool mode) :
    TaskDialog(),
    viewProvider(inVp)
{
    widget  = new TaskGeomHatch(inHatch, inVp, mode);
    taskbox = new Gui::TaskView::TaskBox(Gui::BitmapFactory().pixmap("TechDraw_TreeView"),
                                         widget->windowTitle(), true, nullptr);
    taskbox->groupLayout()->addWidget(widget);
    Content.push_back(taskbox);
    setAutoCloseOnTransactionChange(true);
}

TaskDlgGeomHatch::~TaskDlgGeomHatch()
{
}

void TaskDlgGeomHatch::setCreateMode(bool mode)
{
    widget->setCreateMode(mode);
}

void TaskDlgGeomHatch::update()
{
    //widget->updateTask();
}

//==== calls from the TaskView ===============================================================
void TaskDlgGeomHatch::open()
{
}

void TaskDlgGeomHatch::clicked(int i)
{
    Q_UNUSED(i);
}

bool TaskDlgGeomHatch::accept()
{
    return widget->accept();
}

bool TaskDlgGeomHatch::reject()
{
    return widget->reject();
}

#include <Mod/TechDraw/Gui/moc_TaskGeomHatch.cpp>
