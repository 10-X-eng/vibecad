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

#include <cmath>
#include <QDialog>
#include <QTextEdit>
#include <QGraphicsView>
#include <QMessageBox>
#include <QScrollBar>
#include <QAbstractScrollArea>

#include <App/Document.h>
#include <Base/Console.h>
#include <Base/Interpreter.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/Control.h>
#include <Gui/Document.h>
#include <Gui/MainWindow.h>
#include <Gui/ViewProvider.h>
#include <Mod/TechDraw/App/DrawLeaderLine.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawRichAnno.h>
#include <Mod/TechDraw/App/LineGroup.h>

#include "ui_TaskRichAnno.h" //This will include mrichtextedit.h if the .ui file uses MRichTextEdit
#include "TaskRichAnno.h"
#include "MDIViewPage.h"
#include "PreferencesGui.h"
#include "QGIView.h"
#include "QGIRichAnno.h"
#include "QGMText.h"
#include "QGVPage.h"
#include "QGSPage.h"
#include "Rez.h"
#include "ViewProviderPage.h"
#include "ViewProviderRichAnno.h"


using namespace Gui;
using namespace TechDraw;
using namespace TechDrawGui;

//ctor for edit
TaskRichAnno::TaskRichAnno(TechDrawGui::ViewProviderRichAnno* annoVP) :
    ui(new Ui_TaskRichAnno),
    m_annoVP(annoVP),
    m_baseFeat(nullptr),
    m_basePage(nullptr),
    m_annoFeat(nullptr),
    m_qgParent(nullptr),
    m_createMode(false),
    m_placementMode(false),
    m_inProgressLock(true), // Lock during setup
    m_btnOK(nullptr),
    m_btnCancel(nullptr),
    m_qgiAnno(nullptr),
    m_syncLock(false),
    m_view(nullptr),
    m_toolbar(nullptr)
{
    m_annoFeat = m_annoVP ? m_annoVP->getFeature() : nullptr;
    if (!m_annoVP || !m_annoFeat) {
        throw Base::TypeError(
            "The annotation editor requires a live annotation"
        );
    }
    m_basePage = m_annoFeat->findParentPage();
    //m_baseFeat can be null
    App::DocumentObject* obj = m_annoFeat->AnnoParent.getValue();
    if (obj) {
        if ( obj->isDerivedFrom<TechDraw::DrawView>() )  {
            m_baseFeat = static_cast<TechDraw::DrawView*>(m_annoFeat->AnnoParent.getValue());
        }
    }

    auto* document = m_annoFeat->getDocument();
    if (!m_basePage || m_basePage->getDocument() != document
        || (m_baseFeat
            && (m_baseFeat->getDocument() != document
                || m_baseFeat->findParentPage() != m_basePage))
        || document->getBookedTransactionID()
            == App::NullTransaction) {
        throw Base::RuntimeError(
            "The annotation editor requires an annotation on a live page "
            "and its owning transaction"
        );
    }
    m_documentIdentity =
        TaskInternal::DocumentIdentity(document);
    m_pageIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawPage>(
            m_basePage
        );
    m_baseIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawView>(
            m_baseFeat
        );
    m_annotationIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawRichAnno>(
            m_annoFeat
        );
    Gui::Document* activeGui =
        m_documentIdentity.guiDocument();
    Gui::ViewProvider* vp =
        activeGui ? activeGui->getViewProvider(m_basePage) : nullptr;
    m_vpp = dynamic_cast<ViewProviderPage*>(vp);
    if (!m_vpp || !m_vpp->getQGVPage()
        || !m_vpp->getQGSPage()) {
        throw Base::RuntimeError(
            "The annotation editor could not find the drawing page"
        );
    }
    m_view = m_vpp->getMDIViewPage();

    m_qgParent = nullptr;
    if (m_baseFeat) {
        m_qgParent = m_vpp->getQGSPage()->findQViewForDocObj(m_baseFeat);
    }

    QGVPage* graphicsView = nullptr;
    graphicsView = m_vpp->getQGVPage();
    m_toolbar = new MRichTextEdit(graphicsView->viewport());

    m_tid = document->getBookedTransactionID();


    ui->setupUi(this);

    m_title = QObject::tr("Rich Text Editor");
    setUiEdit();

    finishSetup();
}

//ctor for creation
TaskRichAnno::TaskRichAnno(TechDraw::DrawView* baseFeat,
                           TechDraw::DrawPage* page) :
    ui(new Ui_TaskRichAnno),
    m_annoVP(nullptr),
    m_baseFeat(baseFeat),
    m_basePage(page),
    m_annoFeat(nullptr),
    m_qgParent(nullptr),
    m_createMode(true),
    m_placementMode(true),
    m_inProgressLock(true), // Lock during setup
    m_btnOK(nullptr),
    m_btnCancel(nullptr),
    m_qgiAnno(nullptr),
    m_syncLock(false),
    m_view(nullptr),
    m_toolbar(nullptr)
{
    if (!m_basePage || !m_basePage->getDocument()
        || (m_baseFeat
            && (m_baseFeat->getDocument()
                    != m_basePage->getDocument()
                || m_baseFeat->findParentPage()
                    != m_basePage))
        || m_basePage->getDocument()->getBookedTransactionID()
            == App::NullTransaction) {
        throw Base::RuntimeError(
            "A new annotation requires a live drawing page and its owning "
            "transaction"
        );
    }
    auto* document = m_basePage->getDocument();
    m_documentIdentity =
        TaskInternal::DocumentIdentity(document);
    m_pageIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawPage>(
            m_basePage
        );
    m_baseIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawView>(
            m_baseFeat
        );
    Gui::Document* activeGui =
        m_documentIdentity.guiDocument();
    Gui::ViewProvider* vp =
        activeGui ? activeGui->getViewProvider(m_basePage) : nullptr;
    m_vpp = dynamic_cast<ViewProviderPage*>(vp);
    if (!m_vpp || !m_vpp->getQGVPage()
        || !m_vpp->getQGSPage()) {
        throw Base::RuntimeError(
            "The annotation task could not find the drawing page"
        );
    }
    m_view = m_vpp->getMDIViewPage();

    m_qgParent = nullptr;
    if (m_vpp->getQGSPage()) {
        m_qgParent = m_vpp->getQGSPage()->findQViewForDocObj(baseFeat);
    }

    m_tid = document->getBookedTransactionID();

    ui->setupUi(this);
    m_title = QObject::tr("Rich Text Creator");

    setUiPrimary(); // Sets initial UI values, might trigger signals if connected

    // Don't create the feature or the toolbar yet. Enter placement mode instead.
    QGVPage* graphicsView = m_vpp->getQGVPage();
    if (graphicsView) {
        // Avoid double installation
        if (m_viewport) {
            m_viewport->removeEventFilter(this);
        }
        m_viewport = graphicsView->viewport();
        m_viewport->installEventFilter(this);
    }

    enterPlacementMode();

    m_inProgressLock = false;
}

TaskRichAnno::~TaskRichAnno()
{
    removeViewFilter();
    
    if (m_toolbar) {
        m_toolbar->close();  // This will delete
        m_toolbar = nullptr;
    }
}

bool TaskRichAnno::resolveTargets()
{
    auto* document = m_documentIdentity.resolve();
    auto* page = m_pageIdentity.resolve();
    if (!document || !page
        || page->getDocument() != document) {
        return false;
    }
    TechDraw::DrawView* base = nullptr;
    if (m_baseIdentity.id() >= 0) {
        base = m_baseIdentity.resolve();
        if (!base || base->findParentPage() != page) {
            return false;
        }
    }
    TechDraw::DrawRichAnno* annotation = nullptr;
    if (m_annotationIdentity.id() >= 0) {
        annotation = m_annotationIdentity.resolve();
        if (!annotation
            || annotation->findParentPage() != page) {
            return false;
        }
    }
    auto* guiDocument = m_documentIdentity.guiDocument();
    auto* pageProvider = guiDocument
        ? dynamic_cast<ViewProviderPage*>(
              guiDocument->getViewProvider(page)
          )
        : nullptr;
    if (!pageProvider || !pageProvider->getQGVPage()
        || !pageProvider->getQGSPage()) {
        return false;
    }
    ViewProviderRichAnno* annotationProvider = nullptr;
    if (annotation) {
        annotationProvider =
            dynamic_cast<ViewProviderRichAnno*>(
                guiDocument->getViewProvider(annotation)
            );
        if (!annotationProvider) {
            return false;
        }
    }

    m_basePage = page;
    m_baseFeat = base;
    m_annoFeat = annotation;
    m_annoVP = annotationProvider;
    m_vpp = pageProvider;
    m_view = pageProvider->getMDIViewPage();
    return m_view != nullptr;
}

void TaskRichAnno::finishSetup()
{
    m_inProgressLock = true;  // Lock during setup

    // --- Step 1: Get pointer to the QGIRichAnno object ---
    if (!m_annoVP || !m_view) {
        throw Base::RuntimeError(
            "The annotation editor has no live view provider"
        );
    }
    m_qgiAnno = static_cast<QGIRichAnno*>(m_annoVP->getQView());
    QGVPage* graphicsView = m_view->getViewProviderPage()->getQGVPage();

    if (!m_qgiAnno || !graphicsView) {
        throw Base::RuntimeError(
            "The annotation editor could not create its drawing item"
        );
    }

    m_toolbar->setWindowFlags(Qt::Tool | Qt::FramelessWindowHint | Qt::X11BypassWindowManagerHint);
    m_toolbar->setAttribute(Qt::WA_DeleteOnClose);
    m_toolbar->setProperty("floating", true);

    // --- Step 2: Perform the "hide text area" trick ---
    QTextEdit* textEditChild = m_toolbar->findChild<QTextEdit*>();
    if (!textEditChild) {
        delete m_toolbar;
        m_toolbar = nullptr;
        m_inProgressLock = false;
        return;
    }

    textEditChild->setVisible(false);
    textEditChild->setMinimumHeight(0);
    textEditChild->setMaximumHeight(0);

    m_toolbar->setMinimalMode(true);
    m_toolbar->adjustSize();
    m_toolbar->setFixedSize(m_toolbar->sizeHint());
    m_toolbar->show();

    // --- Step 3: Connect signals ---
    // Get the internal document of the toolbar and link it to the QGIRichAnno
    textEditChild->setDocument(m_qgiAnno->document());

    // Connect signals to keep things in sync
    connect(m_qgiAnno,
            &QGIRichAnno::selectionChanged,
            this,
            &TaskRichAnno::onViewSelectionChanged);
    connect(m_qgiAnno,
            &QGIRichAnno::positionChanged,
            this,
            &TaskRichAnno::onViewPositionChanged);

    // Also connect the width changed signal for resize handles
    connect(m_qgiAnno, &QGIRichAnno::widthChanged, this, &TaskRichAnno::onViewWidthChanged);

    connect(ui->dsbMaxWidth,
            qOverload<double>(&Gui::QuantitySpinBox::valueChanged),
            this,
            &TaskRichAnno::onMaxWidthChanged);
    connect(ui->gbFrame, &QGroupBox::toggled, this, &TaskRichAnno::onShowFrameToggled);
    connect(ui->cpFrameColor, &Gui::ColorButton::changed, this, &TaskRichAnno::onFrameColorChanged);
    connect(ui->dsbWidth,
            qOverload<double>(&Gui::QuantitySpinBox::valueChanged),
            this,
            &TaskRichAnno::onFrameWidthChanged);
    connect(ui->cFrameStyle,
            qOverload<int>(&QComboBox::currentIndexChanged),
            this,
            &TaskRichAnno::onFrameStyleChanged);

    // Panning is detected by scroll bar value changes.
    connect(graphicsView->horizontalScrollBar(),
            &QScrollBar::valueChanged,
            this,
            &TaskRichAnno::onViewTransformed);
    connect(graphicsView->verticalScrollBar(),
            &QScrollBar::valueChanged,
            this,
            &TaskRichAnno::onViewTransformed);

    
    connect(ui->gbFrame, &QGroupBox::toggled, this, &TaskRichAnno::refocusAnnotation);
    connect(ui->cpFrameColor, &Gui::ColorButton::changed, this, &TaskRichAnno::refocusAnnotation);
    connect(ui->cFrameStyle,
            QOverload<int>::of(&QComboBox::activated),
            this,
            &TaskRichAnno::refocusAnnotation);
    connect(ui->dsbMaxWidth,
            &Gui::QuantitySpinBox::editingFinished,
            this,
            &TaskRichAnno::refocusAnnotation);
    connect(ui->dsbWidth,
            &Gui::QuantitySpinBox::editingFinished,
            this,
            &TaskRichAnno::refocusAnnotation);

    onViewSelectionChanged();  // Sync initial cursor to hidden editor
    m_qgiAnno->setEditMode(true);

    if (graphicsView) {
        // Install the event filter on the viewport, which receives the mouse events.
        if (m_viewport) {
            m_viewport->removeEventFilter(this);
        }
        m_viewport = graphicsView->viewport();
        m_viewport->installEventFilter(this);
    }

    QTimer::singleShot(0, m_qgiAnno, &QGIRichAnno::updateLayout);

    m_inProgressLock = false;
}

void TaskRichAnno::onViewTransformed()
{
    // When the view pans, the item's scene position hasn't changed.
    // We just need to re-run the position calculation with its current scenePos.
    if (m_qgiAnno) {
        onViewPositionChanged();
    }
}

void TaskRichAnno::onViewSelectionChanged()
{
    if (m_syncLock) {
        return;
    }

    // When the selection in the view changes, we need to update the
    // toolbar's internal state so the buttons (Bold, etc.) reflect the selection.
    if (m_toolbar && m_qgiAnno) {
        QTextEdit* textEditChild = m_toolbar->findChild<QTextEdit*>();
        if (textEditChild) {
            m_syncLock = true;
            textEditChild->setTextCursor(m_qgiAnno->textCursor());
            m_syncLock = false;
        }
    }

}

void TaskRichAnno::onViewPositionChanged()
{
    // Make sure you have a local variable for the QGVPage to make the code cleaner
    QGVPage* graphicsView = nullptr;
    if (m_view) {
        graphicsView = m_view->getViewProviderPage()->getQGVPage();
    }

    if (m_toolbar && graphicsView && m_qgiAnno) {
        // Get the item's bounding rectangle in Scene coordinates
        QRectF itemRect = m_qgiAnno->mapToScene(m_qgiAnno->boundingRect()).boundingRect();

        // Calculate the top-center point of the item in Scene coordinates
        QPointF topCenterScenePos(itemRect.center().x(), itemRect.top());

        // Map from scene using the correct QGVPage object
        QPoint viewPos = graphicsView->mapFromScene(topCenterScenePos);

        // Map the QGraphicsView point to global screen coordinates
        QPoint globalPos = graphicsView->mapToGlobal(viewPos);

        // Position the toolbar above this point, centered horizontally
        int yOffset = 10;
        QPoint toolbarPos(globalPos.x() - m_toolbar->width() / 2,
                          globalPos.y() - m_toolbar->height() - yOffset);

        m_toolbar->move(toolbarPos);

        // Ensure the toolbar is raised to the top
        m_toolbar->raise();
    }
}

void TaskRichAnno::updateTask()
{
//    blockUpdate = true;
}

void TaskRichAnno::changeEvent(QEvent *event)
{
    if (event->type() == QEvent::LanguageChange) {
        ui->retranslateUi(this);
        // Update titles if they are translatable and set directly
        if (m_createMode) {
            m_title = QObject::tr("Rich text creator");
        } else {
            m_title = QObject::tr("Rich text editor");
        }
        if (m_annoFeat || m_createMode) { // Only set window title if UI is relevant
             setWindowTitle(m_title);
        }
    }
    QWidget::changeEvent(event);
}

void TaskRichAnno::setUiPrimary()
{
    setWindowTitle(m_title);

    if (m_baseFeat) {
        std::string baseName = m_baseFeat->getNameInDocument();
        ui->leBaseView->setText(QString::fromStdString(baseName));
    }
    ui->dsbMaxWidth->setUnit(Base::Unit::Length);
    ui->dsbMaxWidth->setValue(-1.0);

    ui->dsbWidth->setUnit(Base::Unit::Length);
    ui->dsbWidth->setMinimum(0);
    ui->dsbWidth->setValue(prefWeight());


    ui->cpFrameColor->setColor(prefLineColor().asValue<QColor>());
    ui->gbFrame->setChecked(false);
}

void TaskRichAnno::enableTextUi(bool enable)
{
    m_toolbar->setEnabled(enable);
}

void TaskRichAnno::setUiEdit()
{
    setWindowTitle(m_title);
    enableTextUi(true);

    if (m_annoFeat) {
        std::string baseName("None");
        App::DocumentObject* docObj = m_annoFeat->AnnoParent.getValue();
        if (docObj) {
            baseName = docObj->getNameInDocument();
        }
        ui->leBaseView->setText(QString::fromStdString(baseName));
        m_toolbar->setText(QString::fromUtf8(m_annoFeat->AnnoText.getValue()));
        ui->dsbMaxWidth->setValue(m_annoFeat->MaxWidth.getValue());
        ui->gbFrame->setChecked(m_annoFeat->ShowFrame.getValue());
    }

    if (m_annoVP) {
        ui->cpFrameColor->setColor(m_annoVP->LineColor.getValue().asValue<QColor>());
        ui->dsbWidth->setValue(m_annoVP->LineWidth.getValue());
        ui->cFrameStyle->setCurrentIndex(m_annoVP->LineStyle.getValue());
    }
}


void TaskRichAnno::onMaxWidthChanged(double value)
{
    createAnnoIfNotAlready();

    if (m_inProgressLock || !m_annoFeat) return;
    m_annoFeat->MaxWidth.setValue(value);
    m_annoFeat->requestPaint();
}

void TaskRichAnno::onViewWidthChanged()
{
    ui->dsbMaxWidth->blockSignals(true);
    ui->dsbMaxWidth->setValue(m_annoFeat->MaxWidth.getValue());
    ui->dsbMaxWidth->blockSignals(false);
}

void TaskRichAnno::onShowFrameToggled(bool checked)
{
    createAnnoIfNotAlready();

    if (m_inProgressLock || !m_annoFeat) return;
    m_annoFeat->ShowFrame.setValue(checked);
    // Update VP editable status based on ShowFrame
    if (m_annoVP) {
        bool editable = checked;
        m_annoVP->LineWidth.setStatus(App::Property::ReadOnly, !editable);
        m_annoVP->LineStyle.setStatus(App::Property::ReadOnly, !editable);
        m_annoVP->LineColor.setStatus(App::Property::ReadOnly, !editable);
    }
    m_annoFeat->requestPaint();
}

void TaskRichAnno::onFrameColorChanged()
{
    createAnnoIfNotAlready();

    if (m_inProgressLock || !m_annoVP) return;
    Base::Color ac;
    ac.setValue<QColor>(ui->cpFrameColor->color());
    m_annoVP->LineColor.setValue(ac);
    if (m_annoFeat) m_annoFeat->requestPaint();
}

void TaskRichAnno::onFrameWidthChanged(double value)
{
    createAnnoIfNotAlready();

    if (m_inProgressLock || !m_annoVP) return;
    m_annoVP->LineWidth.setValue(value);
     if (m_annoFeat) m_annoFeat->requestPaint();
}

void TaskRichAnno::onFrameStyleChanged(int index)
{
    createAnnoIfNotAlready();

    if (m_inProgressLock || !m_annoVP) return;
    m_annoVP->LineStyle.setValue(index);
    if (m_annoFeat) m_annoFeat->requestPaint();
}

void TaskRichAnno::createAnnoIfNotAlready()
{
    if (m_createMode && m_placementMode) {
        createAndSetupAnnotation(nullptr);
    }
}

double TaskRichAnno::prefWeight() const
{
    return TechDraw::LineGroup::getDefaultWidth("Graphic");
}

Base::Color TaskRichAnno::prefLineColor()
{
    return PreferencesGui::leaderColor();
}

void TaskRichAnno::refocusAnnotation()
{
    // Use a zero-delay timer to schedule the focus change.
    // This allows the current widget interaction (e.g., the checkbox toggling)
    // to complete fully before we shift focus.
    QTimer::singleShot(0, this, [this]() {
        if (m_qgiAnno) {
            m_qgiAnno->refocusAnnotation();
        }
    });
}

void TaskRichAnno::focusOutEvent(QFocusEvent* event)
{
    // Let the base class do its thing first
    QWidget::focusOutEvent(event);

    // If the focus is leaving our task panel for something else,
    // ensure our annotation is the active element.
    refocusAnnotation();
}

bool TaskRichAnno::eventFilter(QObject* watched, QEvent* event)
{
    if (!m_view || !m_view->getViewProviderPage() || !m_viewport) {
        return QWidget::eventFilter(watched, event);
    }

    QGVPage* graphicsView = m_view->getViewProviderPage()->getQGVPage();
    if (!graphicsView) {
        return QWidget::eventFilter(watched, event);
    }
    
    if (watched == m_viewport) {
        if (event->type() == QEvent::Enter) {
            if (!m_placementMode && m_qgiAnno) {
                refocusAnnotation();
            }
        }

        if (event->type() == QEvent::MouseButtonPress) {
            if (m_createMode && m_placementMode) {
                auto mouseEvent = static_cast<QMouseEvent*>(event);
                QPointF scenePos = graphicsView->mapToScene(mouseEvent->pos());
                createAndSetupAnnotation(&scenePos);
                return QWidget::eventFilter(watched, event);
            }

            // Cast the event to get the mouse position
            auto mouseEvent = static_cast<QMouseEvent*>(event);
            QGraphicsItem* item = graphicsView->itemAt(mouseEvent->pos());

            // Walk up the parent chain to see if the click was on our annotation or one of its
            // children.
            QGIRichAnno* clickedAnno = nullptr;
            while (item) {
                clickedAnno = dynamic_cast<QGIRichAnno*>(item);
                if (clickedAnno) {
                    break;  // Found an annotation
                }
                item = item->parentItem();  // Check the parent
            }

            // If we didn't find our specific annotation, the click was "outside".
            if (clickedAnno != m_qgiAnno) {
                // Simulate clicking the "OK" button to accept the changes.
                if (m_btnOK && m_btnOK->isEnabled()) {
                    m_btnOK->click();
                    return true;  // We've handled this event, so stop further processing.
                }
            }
            else if (!m_placementMode && m_qgiAnno) {
                refocusAnnotation();
            }
        }
    }

    // For all other events, pass them on to the default handler.
    return QWidget::eventFilter(watched, event);
}

void TaskRichAnno::removeViewFilter()
{
    if (m_viewport) {
        m_viewport->removeEventFilter(this);
        m_viewport = nullptr;
    }
}

//******************************************************************************
void TaskRichAnno::enterPlacementMode()
{
    if (m_view) {
        if (auto* gv = m_view->getViewProviderPage()->getQGVPage()) {
            gv->viewport()->setCursor(Qt::CrossCursor);
        }
    }
    // Disable UI elements that require an annotation to exist
    ui->gbFrame->setEnabled(false);
    ui->dsbMaxWidth->setEnabled(false);
    setFocus();  // Set focus to the panel to capture key presses
}

void TaskRichAnno::createAndSetupAnnotation(const QPointF* scenePos)
{
    if (!m_placementMode) {
        return;  // Already created
    }
    m_inProgressLock = true;
    m_placementMode = false;

    // Restore cursor
    if (m_view) {
        if (auto* gv = m_view->getViewProviderPage()->getQGVPage()) {
            gv->viewport()->setCursor(Qt::ArrowCursor);
        }
    }

    // Now that the feature exists, create the toolbar and finish setup
    QGVPage* graphicsView = m_vpp->getQGVPage();
    m_toolbar = new MRichTextEdit(graphicsView->viewport());

    try {
        createAnnoFeature(scenePos);
    }
    catch (const Base::Exception& error) {
        m_inProgressLock = false;
        QMessageBox::critical(
            this,
            tr("Annotation Creation Failed"),
            QString::fromUtf8(error.what())
        );
        Gui::Control().reject(m_documentIdentity.resolve());
        return;
    }

    if (!m_annoFeat) {  // Safety check if creation failed
        Base::Console().error("TaskRichAnno - Failed to create annotation feature.\n");
        m_inProgressLock = false;
        Gui::Control().reject(m_documentIdentity.resolve());
        return;
    }

    try {
        finishSetup();
    }
    catch (const Base::Exception& error) {
        m_inProgressLock = false;
        QMessageBox::critical(
            this,
            tr("Annotation Editor Failed"),
            QString::fromUtf8(error.what())
        );
        Gui::Control().reject(m_documentIdentity.resolve());
        return;
    }

    // Re-enable the UI
    ui->gbFrame->setEnabled(true);
    ui->dsbMaxWidth->setEnabled(true);

    // Select the new annotation object so that the frame is shown
    Gui::Selection().addSelection(m_annoFeat->getDocument()->getName(), m_annoFeat->getNameInDocument());

    refocusAnnotation();  // Give focus to the new annotation

    m_inProgressLock = false;
}

void TaskRichAnno::createAnnoFeature(const QPointF* scenePos)
{
    if (!resolveTargets()
        || m_documentIdentity.resolve()->getBookedTransactionID()
            != m_tid) {
        throw Base::RuntimeError(
            "The annotation target or transaction is no longer available"
        );
    }
    auto* document = m_documentIdentity.resolve();
    const std::string objectName{QT_TR_NOOP("RichTextAnnotation")};
    std::string annoName =
        document->getUniqueObjectName(objectName.c_str());
    std::string annoType = "TechDraw::DrawRichAnno";
    const std::string documentName =
        Base::InterpreterSingleton::strToPython(
            document->getName()
        );
    const QString annotationFactory =
        QStringLiteral(
            "App.getDocument('%1').addObject('%2', '%3')"
        )
            .arg(
                QString::fromStdString(documentName),
                QString::fromStdString(annoType),
                QString::fromStdString(annoName)
            );
    auto* object = Gui::Command::runDocumentObjectCommand(
        Command::Doc,
        *document,
        annotationFactory.toUtf8(),
        TechDraw::DrawRichAnno::getClassTypeId()
    );
    m_annoFeat =
        dynamic_cast<TechDraw::DrawRichAnno*>(object);
    if (!m_annoFeat) {
        throw Base::RuntimeError(
            "The annotation object could not be created"
        );
    }
    annoName = m_annoFeat->getNameInDocument();
    const std::string generatedSuffix =
        annoName.rfind(objectName, 0) == 0
        ? annoName.substr(objectName.length())
        : annoName;
    m_annotationIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawRichAnno>(
            m_annoFeat
        );
    const std::string pageCommand =
        Gui::Command::getObjectCmd(m_basePage);
    const std::string annotationCommand =
        Gui::Command::getObjectCmd(m_annoFeat);
    Command::doCommand(
        Command::Doc,
        "%s.addView(%s)",
        pageCommand.c_str(),
        annotationCommand.c_str()
    );

    if (m_baseFeat) {
        const std::string baseCommand =
            Gui::Command::getObjectCmd(m_baseFeat);
        Command::doCommand(
            Command::Doc,
            "%s.AnnoParent = %s",
            annotationCommand.c_str(),
            baseCommand.c_str()
        );
    }
    commonFeatureUpdate();
    if (scenePos) {
        m_annoFeat->X.setValue(Rez::appX(scenePos->x()));
        m_annoFeat->Y.setValue(-Rez::appX(scenePos->y()));
    }
    else if (m_baseFeat) {
        QPointF qTemp = calcTextStartPos(m_annoFeat->getScale());
        Base::Vector3d vTemp(qTemp.x(), qTemp.y());
        m_annoFeat->X.setValue(Rez::appX(vTemp.x));
        m_annoFeat->Y.setValue(Rez::appX(vTemp.y));
    }
    else {
        m_annoFeat->X.setValue(m_basePage->getPageWidth() / 2.0);
        m_annoFeat->Y.setValue(m_basePage->getPageHeight() / 2.0);
    }

    if (m_annoFeat) {
        Gui::ViewProvider* vp = QGIView::getViewProvider(m_annoFeat);
        m_annoVP = freecad_cast<ViewProviderRichAnno*>(vp); // Store m_annoVP
        if (m_annoVP) {
            Base::Color ac;
            ac.setValue<QColor>(ui->cpFrameColor->color());
            m_annoVP->LineColor.setValue(ac);
            m_annoVP->LineWidth.setValue(ui->dsbWidth->rawValue());
            m_annoVP->LineStyle.setValue(ui->cFrameStyle->currentIndex());

            // Set initial VP property readonly state based on ShowFrame
            bool editable = m_annoFeat->ShowFrame.getValue();
            m_annoVP->LineWidth.setStatus(App::Property::ReadOnly, !editable);
            m_annoVP->LineStyle.setStatus(App::Property::ReadOnly, !editable);
            m_annoVP->LineColor.setStatus(App::Property::ReadOnly, !editable);
        }
    }

    std::string translatedObjectName{tr(objectName.c_str()).toStdString()};
    m_annoFeat->Label.setValue(
        translatedObjectName + generatedSuffix
    );

    //trigger claimChildren in tree
    if (m_baseFeat) {
        m_baseFeat->touch();
    }

    m_basePage->touch();

    if (m_annoFeat) {
        m_annoFeat->recomputeFeature();
        if (m_annoFeat->isError()) {
            throw Base::RuntimeError(
                "The annotation could not produce a valid result"
            );
        }
        m_annoFeat->requestPaint();
    }
}

void TaskRichAnno::commonFeatureUpdate()
{
//    Base::Console().message("TRA::commonFeatureUpdate()\n");
    if (!m_annoFeat) return;

    m_annoFeat->AnnoText.setValue(m_toolbar->toHtml().toUtf8());
    m_annoFeat->MaxWidth.setValue(ui->dsbMaxWidth->value().getValue());
    m_annoFeat->ShowFrame.setValue(ui->gbFrame->isChecked());
}

//we don't know the bounding rect of the text, so we have to calculate a reasonable
//guess at the size of the text block.
QPointF TaskRichAnno::calcTextStartPos(double scale)
{
    Q_UNUSED(scale)
//    Base::Console().message("TRA::calcTextStartPos(%.3f)\n", scale);
    double textWidth = 100.0; // Default guess for text width in document units
    double textHeight = 20.0; // Default guess for text height
    double horizGap(Rez::appX(5.0)); // 5mm gap from leader end point in document units

    double annoMaxWidth = ui->dsbMaxWidth->value().getValue(); // MaxWidth from UI
    if (annoMaxWidth > 0 ) {
        textWidth = annoMaxWidth; // Use user-defined MaxWidth if available
    }
    // Note: textHeight is harder to guess accurately without rendering.

    if (m_baseFeat) {
        if (m_baseFeat->isDerivedFrom<TechDraw::DrawLeaderLine>()) {
            TechDraw::DrawLeaderLine* dll = static_cast<TechDraw::DrawLeaderLine*>(m_baseFeat);
            const auto& wayPoints = dll->WayPoints.getValues();
            if (!wayPoints.empty()) {
                Base::Vector3d leaderEndDoc = wayPoints.back(); // Last point of leader in document units
                Base::Vector3d leaderPrevDoc = (wayPoints.size() > 1) ? wayPoints[wayPoints.size()-2] : leaderEndDoc;

                // Position relative to leader end point (these are document units)
                double tPosX_doc, tPosY_doc;

                // Heuristic: place annotation to the right or left of the leader's last segment
                // Anchor point of RichAnno is its center. We want text block (top-left) relative to leader.
                // This calculation is for the *center* of the RichAnno.
                if (leaderEndDoc.x < leaderPrevDoc.x) { // Leader pointing left-ish
                    tPosX_doc = leaderEndDoc.x - horizGap - (textWidth / 2.0);
                } else { // Leader pointing right-ish or vertical
                    tPosX_doc = leaderEndDoc.x + horizGap + (textWidth / 2.0);
                }
                // Vertically, align center of text block slightly below leader end for now
                tPosY_doc = leaderEndDoc.y - (textHeight / 2.0) ;

                return QPointF(Rez::guiX(tPosX_doc), Rez::guiX(tPosY_doc)); // Convert to GUI coords for return
            }
        }
        // If baseFeat is not a leader or has no points, fall through to page center or view center.
        // For a generic DrawView, position near its center.
        double baseX_doc = m_baseFeat->X.getValue();
        double baseY_doc = m_baseFeat->Y.getValue();

        // Position annotation center to the right of baseX,
        // and vertically such that top of annotation is near baseY.
        double tPosX_doc = baseX_doc + (textWidth / 2.0) + horizGap;
        // RichAnno Y is its center. baseY_doc is top of view.
        // So, center of anno = baseY_doc (top of view) + half of anno's estimated height.
        double tPosY_doc = baseY_doc + (textHeight / 2.0);
        return QPointF(Rez::guiX(tPosX_doc), Rez::guiX(tPosY_doc));  // Convert to GUI coords for 
    }

    // Default to page center if no baseFeat
    if (m_basePage) {
        double w_doc = m_basePage->getPageWidth() / 2.0;
        double h_doc = m_basePage->getPageHeight() / 2.0;
        return QPointF(Rez::guiX(w_doc), Rez::guiX(h_doc));
    }

    return QPointF(0.0, 0.0); // Absolute fallback
}

void TaskRichAnno::saveButtons(QPushButton* btnOK,
                             QPushButton* btnCancel)
{
    m_btnOK = btnOK;
    m_btnCancel = btnCancel;
}

void TaskRichAnno::enableTaskButtons(bool enable)
{
    if (m_btnOK) m_btnOK->setEnabled(enable);
    if (m_btnCancel) m_btnCancel->setEnabled(enable);
}

//******************************************************************************

bool TaskRichAnno::accept()
{
    if (m_inProgressLock || !resolveTargets()
        || !m_annoFeat
        || m_documentIdentity.resolve()
                ->getBookedTransactionID()
            != m_tid) {
        return false;
    }

    removeViewFilter();

    if (m_annoVP) {
        if (auto* annotationItem =
                dynamic_cast<QGIRichAnno*>(
                    m_annoVP->getQView()
                )) {
            annotationItem->setEditMode(false);
        }
    }
    commonFeatureUpdate();
    m_annoFeat->recomputeFeature();
    if (m_annoFeat->isError()) {
        QMessageBox::warning(
            this,
            tr("Invalid Annotation"),
            tr("The annotation could not produce a valid result.")
        );
        return false;
    }
    TaskInternal::updateExactDocument(
        m_documentIdentity.resolve()
    );
    TaskInternal::resetExactEdit(
        m_documentIdentity.resolve()
    );

    return true;
}

bool TaskRichAnno::reject()
{
    if (m_inProgressLock) {
        return false;
    }

    if (resolveTargets() && m_annoVP) {
        if (auto* annotationItem =
                dynamic_cast<QGIRichAnno*>(
                    m_annoVP->getQView()
                )) {
            annotationItem->setEditMode(false);
        }
    }
    
    removeViewFilter();
    // TaskView aborts the exact retained transaction after the editor is
    // torn down.  This removes a provisional annotation or restores an edit.
    TaskInternal::resetExactEdit(
        m_documentIdentity.resolve()
    );

    return true;
}

/////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
TaskDlgRichAnno::TaskDlgRichAnno(TechDraw::DrawView* baseFeat,
                                 TechDraw::DrawPage* page)
    : TaskDialog()
{
    widget  = new TaskRichAnno(baseFeat, page);
    taskbox = new Gui::TaskView::TaskBox(Gui::BitmapFactory().pixmap("actions/TechDraw_Annotation"),
                                              widget->windowTitle(), true, nullptr);
    taskbox->groupLayout()->addWidget(widget);
    Content.push_back(taskbox);
    setAutoCloseOnTransactionChange(true);
    if (page) {
        setDocumentName(page->getDocument()->getFullName());
    }
}

TaskDlgRichAnno::TaskDlgRichAnno(TechDrawGui::ViewProviderRichAnno* annoVP)
    : TaskDialog()
{
    widget  = new TaskRichAnno(annoVP);
    taskbox = new Gui::TaskView::TaskBox(Gui::BitmapFactory().pixmap("actions/TechDraw_Annotation"),
                                         widget->windowTitle(), true, nullptr);
    taskbox->groupLayout()->addWidget(widget);
    Content.push_back(taskbox);
    setAutoCloseOnTransactionChange(true);
    setDocumentName(annoVP->getDocument()->getDocument()->getFullName());
}

TaskDlgRichAnno::~TaskDlgRichAnno()
{
}

void TaskDlgRichAnno::modifyStandardButtons(QDialogButtonBox* box)
{
    QPushButton* btnOK = box->button(QDialogButtonBox::Ok);
    QPushButton* btnCancel = box->button(QDialogButtonBox::Cancel);
    widget->saveButtons(btnOK, btnCancel);
}

//==== calls from the TaskView ===============================================================

bool TaskDlgRichAnno::accept()
{
    return widget->accept(); // Delegate to the widget's accept logic
}

bool TaskDlgRichAnno::reject()
{
    return widget->reject(); // Delegate to the widget's reject logic
}

void TaskDlgRichAnno::autoClosedOnTransactionChange()
{
    reject();
}

#include <Mod/TechDraw/Gui/moc_TaskRichAnno.cpp>
