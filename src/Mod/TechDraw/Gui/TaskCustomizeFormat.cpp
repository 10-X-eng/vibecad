/***************************************************************************
 *   Copyright (c) 2021 edi                                                *
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

#include <QMessageBox>

#include <App/DocumentObject.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Mod/TechDraw/App/DrawViewBalloon.h>
#include <Mod/TechDraw/App/DrawViewDimension.h>

#include "FormatBuilder.h"
#include "TaskCustomizeFormat.h"
#include "ui_TaskCustomizeFormat.h"


using namespace Gui;
using namespace TechDraw;
using namespace TechDrawGui;

//===========================================================================
// TaskCustomizeFormat
//===========================================================================

TaskCustomizeFormat::TaskCustomizeFormat(App::DocumentObject * object) :
    selectedObject(object),
    isDimension(true),
    ui(new Ui_TaskCustomizeFormat)
{
    if (!selectedObject
        || (!selectedObject->isDerivedFrom<
                TechDraw::DrawViewDimension>()
            && !selectedObject->isDerivedFrom<
                TechDraw::DrawViewBalloon>())) {
        throw Base::TypeError(
            "Customize Format requires a dimension or balloon"
        );
    }
    m_documentIdentity =
        TaskInternal::DocumentIdentity(
            selectedObject->getDocument()
        );
    m_objectIdentity =
        TaskInternal::ObjectIdentity<App::DocumentObject>(
            selectedObject
        );
    ui->setupUi(this);

    setUiEdit();
}

App::DocumentObject*
TaskCustomizeFormat::resolveObject() const
{
    return m_objectIdentity.resolve();
}

bool TaskCustomizeFormat::validateDimensionFormat(
    const QString& format,
    QString* preview
) const
{
    auto* dimension =
        dynamic_cast<TechDraw::DrawViewDimension*>(
            resolveObject()
        );
    if (!dimension) {
        return false;
    }
    try {
        const auto validation = validateDrawingFormatCustomization(
            dimension,
            format.toUtf8().toStdString()
        );
        if (validation.targetKind != "dimension") {
            return false;
        }
        if (preview) {
            *preview = QString::fromUtf8(
                validation.preview.data(),
                static_cast<qsizetype>(validation.preview.size())
            );
        }
        return true;
    }
    catch (...) {
        return false;
    }
}

TaskCustomizeFormat::~TaskCustomizeFormat()
{

}

void TaskCustomizeFormat::updateTask()
{
//    blockUpdate = true;

//    blockUpdate = false;
}


void TaskCustomizeFormat::changeEvent(QEvent *e)
{
    if (e->type() == QEvent::LanguageChange) {
        ui->retranslateUi(this);
    }
}

void TaskCustomizeFormat::setUiEdit()
{
    setWindowTitle(tr("Customize Format"));
    if (auto dim = dynamic_cast<TechDraw::DrawViewDimension*>(selectedObject))
    {
        isDimension = true;
        std::string dimText = dim->FormatSpec.getStrValue();
        ui->leFormat->setText(QString::fromStdString(dimText));
    }
    else if (auto balloon = dynamic_cast<TechDraw::DrawViewBalloon*>(selectedObject))
    {
        isDimension = false;
        std::string balloonText = balloon->Text.getStrValue();
        ui->leFormat->setText(QString::fromStdString(balloonText));
    }
    // GD&T
    connect(ui->pbA01, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbA02, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbA03, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbA04, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbA05, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbA06, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbA07, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbA08, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbA09, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbA10, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbA11, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbA12, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbA13, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbA14, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    // Modifiers
    connect(ui->pbB01, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbB02, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbB03, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbB04, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbB05, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbB06, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbB07, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbB08, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbB09, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbB10, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbB11, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbB12, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbB13, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbB14, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    // Radius & Diameter
    connect(ui->pbC01, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbC02, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbC03, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbC04, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbC05, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    // Angles
    connect(ui->pbD01, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbD02, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbD03, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbD04, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    // Other
    connect(ui->pbE01, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbE02, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbE03, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbE04, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbE05, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbE06, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbE07, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbE08, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbE09, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    // Greek Letters
    connect(ui->pbF01, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbF02, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbF03, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbF04, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbF05, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbF06, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);
    connect(ui->pbF07, &QPushButton::clicked, this, &TaskCustomizeFormat::onSymbolClicked);

    connect(ui->leFormat, &QLineEdit::textChanged, this, &TaskCustomizeFormat::onFormatChanged);
    onFormatChanged();
}

void TaskCustomizeFormat::onSymbolClicked()
{
    // Slot: a symbol PushButton has been clicked
    QObject* senderObj(this->sender());
    QPushButton* pressedButton = qobject_cast<QPushButton*>(senderObj);
    if (pressedButton)
    {
        QString pbText = pressedButton->text();
        ui->leFormat->insert(pbText);
    }
}

void TaskCustomizeFormat::onFormatChanged()
{
    // Slot: the LineEdit field has been changed
    QString formatPreview = ui->leFormat->text();
    if (isDimension)
    {
        if (!validateDimensionFormat(
                formatPreview,
                &formatPreview
            )) {
            formatPreview = tr("Invalid dimension format");
        }
    }
    ui->lbShowPreview->setText(formatPreview);
}

bool TaskCustomizeFormat::accept()
{
    selectedObject = resolveObject();
    if (!selectedObject) {
        return false;
    }
    // Slot: the OK button has been pressed
    QString formatPreview = ui->leFormat->text();
    std::string formatString = formatPreview.toUtf8().constData();
    if (isDimension
        && !validateDimensionFormat(formatPreview)) {
        QMessageBox::warning(
            this,
            tr("Invalid Format"),
            tr(
                "Use one numeric placeholder such as %f, %.2f, %g, %w, "
                "or %r."
            )
        );
        return false;
    }
    try {
        TaskInternal::OwnedDocumentTransaction transaction(
            m_documentIdentity.resolve(),
            QT_TRANSLATE_NOOP("Command", "Customize Format")
        );
        applyDrawingFormatCustomization(selectedObject, formatString);
        transaction.commit();
    }
    catch (const Base::Exception& error) {
        QMessageBox::critical(
            this,
            tr("Format Update Failed"),
            QString::fromUtf8(error.what())
        );
        return false;
    }
    TaskInternal::updateExactDocument(
        m_documentIdentity.resolve()
    );
    return true;
}

bool TaskCustomizeFormat::reject()
{
    return true;
}

//===========================================================================
// TaskDlgCustomizeFormat
//===========================================================================

TaskDlgCustomizeFormat::TaskDlgCustomizeFormat(App::DocumentObject * object)
    : TaskDialog()
{
    widget  = new TaskCustomizeFormat(object);
    taskbox = new Gui::TaskView::TaskBox(Gui::BitmapFactory().pixmap("TechDraw_ExtensionCustomizeFormat"),
                                             widget->windowTitle(), true, nullptr);
    taskbox->groupLayout()->addWidget(widget);
    Content.push_back(taskbox);
    setAutoCloseOnTransactionChange(true);
}

TaskDlgCustomizeFormat::~TaskDlgCustomizeFormat()
{
}

void TaskDlgCustomizeFormat::update()
{
//    widget->updateTask();
}

//==== calls from the TaskView ===============================================================
void TaskDlgCustomizeFormat::open()
{
}

void TaskDlgCustomizeFormat::clicked(int)
{
}

bool TaskDlgCustomizeFormat::accept()
{
    return widget->accept();
}

bool TaskDlgCustomizeFormat::reject()
{
    return widget->reject();
}

#include <Mod/TechDraw/Gui/moc_TaskCustomizeFormat.cpp>
