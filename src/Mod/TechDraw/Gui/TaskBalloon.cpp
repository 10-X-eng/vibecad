/***************************************************************************
 *   Copyright (c) 2016 WandererFan <wandererfan@gmail.com>                *
 *   Copyright (c) 2019 Franck Jullien <franck.jullien@gmail.com>          *
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

#include <App/Document.h>
#include <Base/Console.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/Document.h>
#include <Gui/Control.h>
#include <Mod/TechDraw/App/DrawViewBalloon.h>

#include "TaskBalloon.h"
#include "ui_TaskBalloon.h"
#include "DrawGuiUtil.h"
#include "QGIViewBalloon.h"
#include "ViewProviderBalloon.h"


using namespace Gui;
using namespace TechDraw;
using namespace TechDrawGui;

TaskBalloon::TaskBalloon(QGIViewBalloon *parent, ViewProviderBalloon *balloonVP) :
    ui(new Ui_TaskBalloon)
{
    auto* balloon =
        parent ? parent->getBalloonFeat() : nullptr;
    if (!balloon || !balloonVP
        || balloonVP->getObject() != balloon
        || balloon->getDocument()->getBookedTransactionID()
            == App::NullTransaction) {
        throw Base::RuntimeError(
            "The balloon editor requires a live balloon and its owning "
            "transaction"
        );
    }
    int i = 0;
    m_parent = parent;
    m_balloonVP = balloonVP;
    m_guiDocument = balloonVP->getDocument();
    m_appDocument = parent->getBalloonFeat()->getDocument();
    m_balloonName = parent->getBalloonFeat()->getNameInDocument();
    m_documentIdentity =
        TaskInternal::DocumentIdentity(m_appDocument);
    m_balloonIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawViewBalloon>(
            balloon
        );

    ui->setupUi(this);

    ui->qsbShapeScale->setValue(parent->getBalloonFeat()->ShapeScale.getValue());
    connect(ui->qsbShapeScale, qOverload<double>(&QuantitySpinBox::valueChanged), this, &TaskBalloon::onShapeScaleChanged);

    ui->qsbSymbolScale->setValue(parent->getBalloonFeat()->EndTypeScale.getValue());
    connect(ui->qsbSymbolScale, qOverload<double>(&QuantitySpinBox::valueChanged), this, &TaskBalloon::onEndSymbolScaleChanged);

    std::string value = parent->getBalloonFeat()->Text.getValue();
    QString qs = QString::fromUtf8(value.data(), value.size());
    ui->leText->setText(qs);
    ui->leText->selectAll();
    connect(ui->leText, &QLineEdit::textChanged, this, &TaskBalloon::onTextChanged);
    QTimer::singleShot(0, ui->leText, qOverload<>(&QLineEdit::setFocus));

    DrawGuiUtil::loadArrowBox(ui->comboEndSymbol);
    i = parent->getBalloonFeat()->EndType.getValue();
    ui->comboEndSymbol->setCurrentIndex(i);
    connect(ui->comboEndSymbol, qOverload<int>(&QComboBox::currentIndexChanged), this, &TaskBalloon::onEndSymbolChanged);

    DrawGuiUtil::loadBalloonShapeBox(ui->comboBubbleShape);
    i = parent->getBalloonFeat()->BubbleShape.getValue();
    ui->comboBubbleShape->setCurrentIndex(i);
    connect(ui->comboBubbleShape, qOverload<int>(&QComboBox::currentIndexChanged), this, &TaskBalloon::onBubbleShapeChanged);

    ui->qsbFontSize->setUnit(Base::Unit::Length);
    ui->qsbFontSize->setMinimum(0);

    ui->qsbLineWidth->setUnit(Base::Unit::Length);
    ui->qsbLineWidth->setSingleStep(0.100);
    ui->qsbLineWidth->setMinimum(0);

    ui->gbLeader->setChecked(balloonVP->LineVisible.getValue() != 0);

    // negative kink length is allowed, thus no minimum
    ui->qsbKinkLength->setUnit(Base::Unit::Length);

    if (balloonVP) {
        ui->textColor->setColor(balloonVP->Color.getValue().asValue<QColor>());
        connect(ui->textColor, &ColorButton::changed, this, &TaskBalloon::onColorChanged);
        ui->qsbFontSize->setValue(balloonVP->Fontsize.getValue());
        ui->qsbLineWidth->setValue(balloonVP->LineWidth.getValue());
    }
    // new balloons have already the preferences BalloonKink length
    ui->qsbKinkLength->setValue(parent->getBalloonFeat()->KinkLength.getValue());

    connect(ui->qsbFontSize, qOverload<double>(&QuantitySpinBox::valueChanged), this, &TaskBalloon::onFontsizeChanged);
    connect(ui->gbLeader,&QGroupBox::toggled, this, &TaskBalloon::onLineVisibleChanged);
    connect(ui->qsbLineWidth, qOverload<double>(&QuantitySpinBox::valueChanged), this, &TaskBalloon::onLineWidthChanged);
    connect(ui->qsbKinkLength, qOverload<double>(&QuantitySpinBox::valueChanged), this, &TaskBalloon::onKinkLengthChanged);

    onLineVisibleChanged(ui->gbLeader->isChecked());
}

TaskBalloon::~TaskBalloon()
{
}

TechDraw::DrawViewBalloon*
TaskBalloon::resolveBalloon() const
{
    return m_balloonIdentity.resolve();
}

ViewProviderBalloon*
TaskBalloon::resolveViewProvider() const
{
    auto* balloon = resolveBalloon();
    return balloon && m_balloonVP
        && m_balloonVP->getObject() == balloon
        ? m_balloonVP
        : nullptr;
}

bool TaskBalloon::accept()
{
    auto* balloon = resolveBalloon();
    if (!balloon || !resolveViewProvider()) {
        return false;
    }
    balloon->purgeTouched();
    TaskInternal::updateExactDocument(
        m_documentIdentity.resolve()
    );
    TaskInternal::resetExactEdit(
        m_documentIdentity.resolve()
    );

    return true;
}

bool TaskBalloon::reject()
{
    // TaskView restores the exact retained edit transaction after teardown.
    TaskInternal::resetExactEdit(
        m_documentIdentity.resolve()
    );
    return true;
}

void TaskBalloon::recomputeFeature()
{
    if (auto* balloon = resolveBalloon()) {
        balloon->recomputeFeature();
    }
}

void TaskBalloon::onTextChanged()
{
    auto* balloon = resolveBalloon();
    if (!balloon) {
        return;
    }
    balloon->Text.setValue(
        ui->leText->text().toUtf8().constData()
    );
    recomputeFeature();
}

void TaskBalloon::onColorChanged()
{
    auto* viewProvider = resolveViewProvider();
    if (!viewProvider) {
        return;
    }
    Base::Color ac;
    ac.setValue<QColor>(ui->textColor->color());
    viewProvider->Color.setValue(ac);
    recomputeFeature();
}

void TaskBalloon::onFontsizeChanged()
{
    auto* viewProvider = resolveViewProvider();
    if (!viewProvider) {
        return;
    }
    viewProvider->Fontsize.setValue(
        ui->qsbFontSize->value().getValue()
    );
    recomputeFeature();
}

void TaskBalloon::onBubbleShapeChanged()
{
    auto* balloon = resolveBalloon();
    if (!balloon) {
        return;
    }
    balloon->BubbleShape.setValue(
        ui->comboBubbleShape->currentIndex()
    );
    recomputeFeature();
}

void TaskBalloon::onShapeScaleChanged()
{
    auto* balloon = resolveBalloon();
    if (!balloon) {
        return;
    }
    balloon->ShapeScale.setValue(
        ui->qsbShapeScale->value().getValue()
    );
    recomputeFeature();
}

void TaskBalloon::onEndSymbolChanged()
{
    auto* balloon = resolveBalloon();
    if (!balloon) {
        return;
    }
    balloon->EndType.setValue(
        ui->comboEndSymbol->currentIndex()
    );
    recomputeFeature();
}

void TaskBalloon::onEndSymbolScaleChanged()
{
    auto* balloon = resolveBalloon();
    if (!balloon) {
        return;
    }
    balloon->EndTypeScale.setValue(
        ui->qsbSymbolScale->value().getValue()
    );
    recomputeFeature();
}

void TaskBalloon::onLineVisibleChanged(bool isVisible)
{
    auto* viewProvider = resolveViewProvider();
    if (!viewProvider) {
        return;
    }
    viewProvider->LineVisible.setValue(isVisible ? 1 : 0);
    recomputeFeature();
}

void TaskBalloon::onLineWidthChanged()
{
    auto* viewProvider = resolveViewProvider();
    if (!viewProvider) {
        return;
    }
    viewProvider->LineWidth.setValue(
        ui->qsbLineWidth->value().getValue()
    );
    recomputeFeature();
}

void TaskBalloon::onKinkLengthChanged()
{
    auto* balloon = resolveBalloon();
    if (!balloon) {
        return;
    }
    balloon->KinkLength.setValue(
        ui->qsbKinkLength->value().getValue()
    );
    recomputeFeature();
}


/////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
TaskDlgBalloon::TaskDlgBalloon(QGIViewBalloon *parent, ViewProviderBalloon *balloonVP) :
    TaskDialog()
{
    widget  = new TaskBalloon(parent, balloonVP);
    taskbox = new Gui::TaskView::TaskBox(Gui::BitmapFactory().pixmap("TechDraw_Balloon"), widget->windowTitle(), true, nullptr);
    taskbox->groupLayout()->addWidget(widget);
    Content.push_back(taskbox);
    setAutoCloseOnTransactionChange(true);
}

TaskDlgBalloon::~TaskDlgBalloon()
{
}

void TaskDlgBalloon::update()
{
    //widget->updateTask();
}

//==== calls from the TaskView ===============================================================
void TaskDlgBalloon::open()
{
}

void TaskDlgBalloon::clicked(int i)
{
    Q_UNUSED(i);
}

bool TaskDlgBalloon::accept()
{
    return widget->accept();
}

bool TaskDlgBalloon::reject()
{
    return widget->reject();
}

#include <Mod/TechDraw/Gui/moc_TaskBalloon.cpp>
