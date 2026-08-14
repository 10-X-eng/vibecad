/***************************************************************************
 *   Copyright (c) 2002 Jürgen Riegel <juergen.riegel@web.de>              *
 *   Copyright (c) 2014 Luke Parry <l.parry@warwick.ac.uk>                 *
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

#include <QApplication>
#include <QFileInfo>
#include <QMessageBox>
#include <QCheckBox>
#include <QPushButton>
#include <vector>


#include <App/Document.h>
#include <App/DocumentObject.h>
#include <App/DocumentTimeline.h>
#include <App/Link.h>

#include <Base/Console.h>
#include <Base/Interpreter.h>
#include <Base/Tools.h>

#include <Gui/Action.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/Control.h>
#include <Gui/Document.h>
#include <Gui/FileDialog.h>
#include <Gui/MainWindow.h>
#include <Gui/Selection/Selection.h>
#include <Gui/Selection/SelectionObject.h>
#include <Gui/ViewProvider.h>
#include <Gui/WaitCursor.h>

#include <Mod/Spreadsheet/App/Sheet.h>

#include <Mod/TechDraw/App/DrawComplexSection.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawProjGroup.h>
#include <Mod/TechDraw/App/DrawProjGroupItem.h>
#include <Mod/TechDraw/App/DrawUtil.h>
#include <Mod/TechDraw/App/DrawSVGTemplate.h>
#include <Mod/TechDraw/App/DrawViewClip.h>
#include <Mod/TechDraw/App/DrawViewDetail.h>
#include <Mod/TechDraw/App/DrawViewDraft.h>
#include <Mod/TechDraw/App/DrawViewPart.h>
#include <Mod/TechDraw/App/DrawViewSpreadsheet.h>
#include <Mod/TechDraw/App/DrawViewSymbol.h>
#include <Mod/TechDraw/App/Preferences.h>
#include <Mod/TechDraw/App/DrawBrokenView.h>

#include "DrawGuiUtil.h"
#include "MDIViewPage.h"
#include "QGIViewPart.h"
#include "QGSPage.h"
#include "QGVPage.h"
#include "Rez.h"
#include "TaskActiveView.h"
#include "TaskComplexSection.h"
#include "TaskDetail.h"
#include "TaskDocumentGuard.h"
#include "TaskProjGroup.h"
#include "TaskProjection.h"
#include "TaskSectionView.h"
#include "ViewProviderPage.h"
#include "ViewProviderDrawingView.h"
#include "CommandHelpers.h"

void execSimpleSection(Gui::Command* cmd);
void execComplexSection(Gui::Command* cmd);
void getSelectedShapes(Gui::Command* cmd,
                      std::vector<App::DocumentObject*>& shapes,
                      std::vector<App::DocumentObject*>& xShapes,
                      App::DocumentObject*& faceObj,
                      std::string& faceName);

std::pair<App::DocumentObject*, std::string> faceFromSelection();
std::pair<Base::Vector3d, Base::Vector3d> viewDirection();
Base::Vector3d checkDirectionVsBasis(Base::Vector3d dir);

class Vertex;
using namespace TechDrawGui;
using namespace TechDraw;
using DU = DrawUtil;

namespace
{

class ScopedDocumentStatus
{
public:
    ScopedDocumentStatus(
        App::Document& document,
        App::Document::Status status,
        bool enabled
    )
        : document(&document)
        , status(status)
        , original(document.testStatus(status))
    {
        document.setStatus(status, enabled);
    }

    ~ScopedDocumentStatus()
    {
        restore();
    }

    ScopedDocumentStatus(const ScopedDocumentStatus&) = delete;
    ScopedDocumentStatus& operator=(const ScopedDocumentStatus&) = delete;

    void restore()
    {
        if (!document) {
            return;
        }
        document->setStatus(status, original);
        document = nullptr;
    }

private:
    App::Document* document;
    App::Document::Status status;
    bool original;
};

}  // namespace

//===========================================================================
// TechDraw_PageDefault
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawPageDefault)

CmdTechDrawPageDefault::CmdTechDrawPageDefault() : Command("TechDraw_PageDefault")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("New Page");
    sToolTipText = QT_TR_NOOP("Creates a new page with the default template");
    sWhatsThis = "TechDraw_PageDefault";
    sStatusTip = sToolTipText;
    sPixmap = "actions/TechDraw_PageDefault";
}

void CmdTechDrawPageDefault::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    App::Document* document = getDocument();
    if (!document) {
        return;
    }
    QString templateFileName = Preferences::defaultTemplate();
    QFileInfo tfi(templateFileName);
    if (tfi.isReadable()) {
        Gui::WaitCursor wc;
        try {
            TaskInternal::OwnedDocumentTransaction transaction(
                document,
                QT_TRANSLATE_NOOP("Command", "Drawing create page")
            );

            auto* svgTemplate =
                document->addObject<TechDraw::DrawSVGTemplate>(
                    "Template"
                );
            if (!svgTemplate) {
                throw Base::TypeError(
                    "The drawing template could not be created"
                );
            }
            svgTemplate->translateLabel(
                "DrawSVGTemplate",
                "Template",
                svgTemplate->getNameInDocument()
            );

            auto* page =
                document->addObject<TechDraw::DrawPage>("Page");
            if (!page) {
                throw Base::TypeError(
                    "The drawing page could not be created"
                );
            }
            page->translateLabel(
                "DrawPage",
                "Page",
                page->getNameInDocument()
            );

            page->Template.setValue(svgTemplate);
            DU::markAsTimelineResource(svgTemplate, page);
            auto filespec = DU::cleanFilespecBackslash(
                templateFileName.toStdString()
            );
            svgTemplate->Template.setValue(filespec);
            auto* timeline = App::DocumentTimeline::get(document);
            if (!timeline) {
                throw Base::RuntimeError(
                    "The drawing page could not access document history"
                );
            }
            timeline->finalizeProvisionalOperationBlock(
                page,
                {svgTemplate, page}
            );

            TaskInternal::updateExactDocument(document);
            transaction.commit();

            auto* pageProvider =
                dynamic_cast<TechDrawGui::ViewProviderPage*>(
                    Gui::Application::Instance->getViewProvider(page)
                );
            if (pageProvider) {
                pageProvider->show();
            }
        }
        catch (const Base::Exception& error) {
            QMessageBox::critical(
                Gui::getMainWindow(),
                QObject::tr("Create drawing page"),
                QString::fromUtf8(error.what())
            );
        }
    }
    else {
        QMessageBox::critical(Gui::getMainWindow(), QLatin1String("No template"),
                              QLatin1String("No default template found"));
    }
}

bool CmdTechDrawPageDefault::isActive() { return hasActiveDocument(); }

//===========================================================================
// TechDraw_PageTemplate
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawPageTemplate)

CmdTechDrawPageTemplate::CmdTechDrawPageTemplate() : Command("TechDraw_PageTemplate")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("New Page From Template");
    sToolTipText = QT_TR_NOOP("Creates a new page from a custom template");
    sWhatsThis = "TechDraw_PageTemplate";
    sStatusTip = sToolTipText;
    sPixmap = "actions/TechDraw_PageTemplate";
}

void CmdTechDrawPageTemplate::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    TaskInternal::DocumentIdentity targetDocument(getDocument());
    if (!targetDocument.resolve()) {
        return;
    }
    QString work_dir = Gui::FileDialog::getWorkingDirectory();
    QString templateDir = Preferences::defaultTemplateDir();
    QString templateFileName = Gui::FileDialog::getOpenFileName(
        Gui::getMainWindow(), QObject::tr("Select a template file"), templateDir,
        Gui::FileDialog::FilterList{{QObject::tr("Template"), {"*.svg"}}});
    Gui::FileDialog::setWorkingDirectory(work_dir);// Don't overwrite WD with templateDir

    if (templateFileName.isEmpty()) {
        return;
    }

    QFileInfo tfi(templateFileName);
    if (tfi.isReadable()) {
        Gui::WaitCursor wc;
        try {
            App::Document* document = targetDocument.resolve();
            if (!document) {
                throw Base::RuntimeError(
                    "The target document was closed"
                );
            }
            TaskInternal::OwnedDocumentTransaction transaction(
                document,
                QT_TRANSLATE_NOOP("Command", "Drawing create page")
            );

            auto* svgTemplate =
                document->addObject<TechDraw::DrawSVGTemplate>(
                    "Template"
                );
            if (!svgTemplate) {
                throw Base::TypeError(
                    "The drawing template could not be created"
                );
            }
            svgTemplate->translateLabel(
                "DrawSVGTemplate",
                "Template",
                svgTemplate->getNameInDocument()
            );

            auto* page =
                document->addObject<TechDraw::DrawPage>("Page");
            if (!page) {
                throw Base::TypeError(
                    "The drawing page could not be created"
                );
            }
            page->translateLabel(
                "DrawPage",
                "Page",
                page->getNameInDocument()
            );

            page->Template.setValue(svgTemplate);
            DU::markAsTimelineResource(svgTemplate, page);
            auto filespec = DU::cleanFilespecBackslash(
                templateFileName.toStdString()
            );
            svgTemplate->Template.setValue(filespec);
            auto* timeline = App::DocumentTimeline::get(document);
            if (!timeline) {
                throw Base::RuntimeError(
                    "The drawing page could not access document history"
                );
            }
            timeline->finalizeProvisionalOperationBlock(
                page,
                {svgTemplate, page}
            );

            TaskInternal::updateExactDocument(document);
            transaction.commit();

            auto* pageProvider =
                dynamic_cast<TechDrawGui::ViewProviderPage*>(
                    Gui::Application::Instance->getViewProvider(page)
                );
            if (pageProvider) {
                pageProvider->show();
            }
        }
        catch (const Base::Exception& error) {
            QMessageBox::critical(
                Gui::getMainWindow(),
                QObject::tr("Create drawing page"),
                QString::fromUtf8(error.what())
            );
        }
    }
    else {
        QMessageBox::critical(Gui::getMainWindow(), QLatin1String("No template"),
                              QLatin1String("Template file is invalid"));
    }
}

bool CmdTechDrawPageTemplate::isActive() { return hasActiveDocument(); }

//===========================================================================
// TechDraw_RedrawPage
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawRedrawPage)

CmdTechDrawRedrawPage::CmdTechDrawRedrawPage() : Command("TechDraw_RedrawPage")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Redraw Page");
    sToolTipText = QT_TR_NOOP("Redraws the current page");
    sWhatsThis = "TechDraw_RedrawPage";
    sStatusTip = sToolTipText;
    sPixmap = "actions/TechDraw_RedrawPage";
}

void CmdTechDrawRedrawPage::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    TechDraw::DrawPage* page = DrawGuiUtil::findPage(this);
    if (!page) {
        return;
    }
    Gui::WaitCursor wc;

    page->redrawCommand();
}

bool CmdTechDrawRedrawPage::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this, false);
    return (havePage && haveView);
}

//===========================================================================
// TechDraw_PrintAll
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawPrintAll)

CmdTechDrawPrintAll::CmdTechDrawPrintAll() : Command("TechDraw_PrintAll")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Print All Pages");
    sToolTipText = QT_TR_NOOP("Prints all pages with the print dialog");
    sWhatsThis = "TechDraw_PrintAll";
    sStatusTip = sToolTipText;
    sPixmap = "actions/TechDraw_PrintAll";
}

void CmdTechDrawPrintAll::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    MDIViewPage::printAllPages(getDocument());
}

bool CmdTechDrawPrintAll::isActive() { return DrawGuiUtil::needPage(this); }

//===========================================================================
// TechDraw_View
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawView)

CmdTechDrawView::CmdTechDrawView() : Command("TechDraw_View")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("New View");
    sToolTipText = QT_TR_NOOP(
        "Inserts a new view into the current page based on the selected object in the tree view "
        "or 3D view.\n"
        "If no object is selected, a file browser opens to select an SVG or image file.");
    sWhatsThis = "TechDraw_View";
    sStatusTip = sToolTipText;
    sPixmap = "actions/TechDraw_View";
}

void CmdTechDrawView::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    TechDraw::DrawPage* page = DrawGuiUtil::findPage(this);
    if (!page) {
        return;
    }
    App::Document* document = page->getDocument();
    if (!document) {
        return;
    }

    // switch to the page if it's not current active window
    auto* vpp = freecad_cast<ViewProviderPage*>
        (Gui::Application::Instance->getViewProvider(page));
    if (vpp) {
        vpp->show();
    }


    //set projection direction from selected Face
    //use first object with a face selected
    std::vector<App::DocumentObject*> shapes, xShapes;
    std::vector<Spreadsheet::Sheet*> spreadsheets;
    App::DocumentObject* partObj = nullptr;
    std::string faceName;
    auto selection = getSelection().getSelectionEx(nullptr, App::DocumentObject::getClassTypeId());
    for (auto& sel : selection) {
        bool is_linked = false;
        auto obj = sel.getObject();
        if (!obj) {
            continue;
        }
        if (obj->isDerivedFrom<TechDraw::DrawPage>() || obj->isDerivedFrom<TechDraw::DrawView>()) {
            continue;
        }

        if (auto* spreadsheet =
                dynamic_cast<Spreadsheet::Sheet*>(obj)) {
            if (spreadsheet->getDocument() == document) {
                spreadsheets.push_back(spreadsheet);
            }
            continue;
        }

        if (obj->isDerivedFrom<App::LinkElement>()
            || obj->isDerivedFrom<App::LinkGroup>()
            || obj->isDerivedFrom<App::Link>()) {
            is_linked = true;
        }
        // If parent of the obj is a link to another document, we possibly need to treat non-link obj as linked, too
        // 1st, is obj in another document?
        if (obj->getDocument() != document) {
            std::set<App::DocumentObject*> parents = obj->getInListEx(true);
            for (auto& parent : parents) {
                // Only consider parents in the current document, i.e. possible links in this View's document
                if (parent->getDocument() != document) {
                    continue;
                }
                // 2nd, do we really have a link to obj?
                if (parent->isDerivedFrom<App::LinkElement>()
                    || parent->isDerivedFrom<App::LinkGroup>()
                    || parent->isDerivedFrom<App::Link>()) {
                    // We have a link chain from this document to obj, and obj is in another document -> it is an XLink target
                    is_linked = true;
                }
            }
        }
        if (is_linked) {
            xShapes.push_back(obj);
            continue;
        }
        //not a Link and not null.  assume to be drawable.  Undrawables will be
        // skipped later.
        shapes.push_back(obj);
        if (partObj) {
            continue;
        }
        //don't know if this works for an XLink
        for (auto& sub : sel.getSubNames()) {
            if (TechDraw::DrawUtil::getGeomTypeFromName(sub) == "Face") {
                faceName = sub;
                //
                partObj = obj;
                break;
            }
        }
    }

    ParameterGrp::handle hGrp =
        App::GetApplication().GetParameterGroupByPath(
            "User parameter:BaseApp/Preferences/Mod/TechDraw"
        );
    if (shapes.empty() && xShapes.empty()
        && spreadsheets.empty()) {
        // If nothing was selected, offer a file-backed drawing view.
        bool dontShowAgain =
            hGrp->GetBool("DontShowInsertFileMessage", false);
        if (!dontShowAgain) {
            auto msgText = QObject::tr(
                "To insert a view from existing objects, select them before "
                "invoking this tool. Without a selection, a file browser "
                "will open to insert an SVG or image file."
            );
            QMessageBox msgBox(Gui::getMainWindow());
            msgBox.setText(msgText);
            auto dontShowMsg =
                QObject::tr("Do not show this message again");
            QCheckBox dontShowCheckBox(dontShowMsg, &msgBox);
            msgBox.setCheckBox(&dontShowCheckBox);
            QPushButton* okButton =
                msgBox.addButton(QMessageBox::Ok);
            msgBox.exec();
            if (msgBox.clickedButton() == okButton
                && dontShowCheckBox.isChecked()) {
                hGrp->SetBool("DontShowInsertFileMessage", true);
            }
        }

        const Gui::FileDialog::FilterList filterList {
            {
                QObject::tr("SVG or Image files"),
                {"*.svg", "*.svgz", "*.jpg", "*.jpeg", "*.png", "*.bmp"}
            },
            Gui::FileDialog::Filter::AllFiles(),
        };
        QString filename = Gui::FileDialog::getOpenFileName(
            Gui::getMainWindow(),
            QObject::tr("Select a SVG or Image file to open"),
            Preferences::defaultSymbolDir(),
            filterList
        );
        if (filename.isEmpty()) {
            return;
        }
        if (document->getBookedTransactionID()
            != App::NullTransaction) {
            QMessageBox::warning(
                Gui::getMainWindow(),
                QObject::tr("Task in progress"),
                QObject::tr(
                    "Finish the current operation before inserting a file."
                )
            );
            return;
        }

        const bool isSvg =
            filename.endsWith(
                QStringLiteral(".svg"),
                Qt::CaseInsensitive
            )
            || filename.endsWith(
                QStringLiteral(".svgz"),
                Qt::CaseInsensitive
            );
        const int transactionId = openCommand(
            document,
            isSvg
                ? QT_TRANSLATE_NOOP("Command", "Create Symbol")
                : QT_TRANSLATE_NOOP("Command", "Create image")
        );
        if (transactionId == App::NullTransaction) {
            return;
        }
        try {
            const char* baseName = isSvg ? "Symbol" : "Image";
            const char* typeName =
                isSvg
                ? "TechDraw::DrawViewSymbol"
                : "TechDraw::DrawViewImage";
            const std::string featureName =
                document->getUniqueObjectName(baseName);
            const std::string documentName =
                Base::InterpreterSingleton::strToPython(
                    document->getName()
                );
            const QString viewFactory =
                QStringLiteral(
                    "App.getDocument('%1').addObject('%2', '%3')"
                )
                    .arg(
                        QString::fromStdString(documentName),
                        QString::fromLatin1(typeName),
                        QString::fromStdString(featureName)
                    );
            auto* inserted =
                dynamic_cast<TechDraw::DrawView*>(
                    Gui::Command::runDocumentObjectCommand(
                        Doc,
                        *document,
                        viewFactory.toUtf8(),
                        TechDraw::DrawView::getClassTypeId()
                    )
                );
            if (!inserted) {
                throw Base::RuntimeError(
                    "The drawing file view could not be created"
                );
            }
            inserted->translateLabel(
                isSvg ? "DrawViewSymbol" : "DrawViewImage",
                baseName,
                inserted->getNameInDocument()
            );
            const std::string insertedCommand =
                Gui::Command::getObjectCmd(inserted);
            const std::string pageCommand =
                Gui::Command::getObjectCmd(page);
            const auto filespec = DU::cleanFilespecBackslash(
                Base::Tools::escapeEncodeFilename(
                    filename.toStdString()
                )
            );
            const std::string pythonFilespec =
                Base::InterpreterSingleton::strToPython(filespec);
            if (isSvg) {
                doCommand(Doc, "import codecs");
                doCommand(
                    Doc,
                    "f = codecs.open('%s', 'r', encoding='utf-8')",
                    pythonFilespec.c_str()
                );
                doCommand(Doc, "svg = f.read()");
                doCommand(Doc, "f.close()");
                doCommand(
                    Doc,
                    "%s.Symbol = svg",
                    insertedCommand.c_str()
                );
            }
            else {
                doCommand(
                    Doc,
                    "%s.ImageFile = '%s'",
                    insertedCommand.c_str(),
                    pythonFilespec.c_str()
                );
            }
            doCommand(
                Doc,
                "%s.addView(%s)",
                pageCommand.c_str(),
                insertedCommand.c_str()
            );
            TaskInternal::updateExactDocument(document);
            commitCommand();
        }
        catch (...) {
            abortCommand();
            throw;
        }
        return;
    }

    if (document->getBookedTransactionID()
        != App::NullTransaction) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Task in progress"),
            QObject::tr(
                "Finish the current operation before inserting drawing "
                "views."
            )
        );
        return;
    }

    Gui::WaitCursor wc;
    const int transactionId = openCommand(document, QT_TRANSLATE_NOOP("Command", "Create view"));
    if (transactionId == App::NullTransaction) {
        return;
    }
    try {
        const std::string pageCommand = Gui::Command::getObjectCmd(page);
        const std::string documentName = Base::InterpreterSingleton::strToPython(document->getName());
        std::vector<App::DocumentObject*> createdViews;
        createdViews.reserve(spreadsheets.size() + ((!shapes.empty() || !xShapes.empty()) ? 1 : 0));
        for (auto* spreadsheet : spreadsheets) {
            const std::string featureName = document->getUniqueObjectName("Sheet");
            const QString spreadsheetViewFactory =
                QStringLiteral(
                    "App.getDocument('%1').addObject"
                    "('TechDraw::DrawViewSpreadsheet', '%2')"
                )
                    .arg(
                        QString::fromStdString(documentName),
                        QString::fromStdString(featureName)
                    );
            auto* sheetView =
                dynamic_cast<TechDraw::DrawViewSpreadsheet*>(
                    Gui::Command::runDocumentObjectCommand(
                        Doc,
                        *document,
                        spreadsheetViewFactory.toUtf8(),
                        TechDraw::DrawViewSpreadsheet::getClassTypeId()
                    )
                );
            if (!sheetView) {
                throw Base::RuntimeError("The spreadsheet drawing view could not be created");
            }
            sheetView->translateLabel(
                "DrawViewSpreadsheet",
                "Sheet",
                sheetView->getNameInDocument()
            );
            const std::string sheetViewCommand = Gui::Command::getObjectCmd(sheetView);
            const std::string spreadsheetCommand = Gui::Command::getObjectCmd(spreadsheet);
            doCommand(Doc, "%s.Source = %s", sheetViewCommand.c_str(), spreadsheetCommand.c_str());
            doCommand(Doc, "%s.addView(%s)", pageCommand.c_str(), sheetViewCommand.c_str());
            doCommand(
                Doc,
                "if %s.Scale: %s.Scale = %s.Scale",
                pageCommand.c_str(),
                sheetViewCommand.c_str(),
                pageCommand.c_str()
            );
            createdViews.push_back(sheetView);
        }

        if (shapes.empty() && xShapes.empty()) {
            CommandHelpers::groupTimelineOutputs(
                document,
                createdViews,
                "DrawingViews",
                QT_TRANSLATE_NOOP("Command", "Drawing Views")
            );
            TaskInternal::updateExactDocument(document);
            commitCommand();
            return;
        }

        const std::string featureName = document->getUniqueObjectName("View");
        const QString partViewFactory =
            QStringLiteral(
                "App.getDocument('%1').addObject"
                "('TechDraw::DrawProjGroupItem', '%2')"
            )
                .arg(
                    QString::fromStdString(documentName),
                    QString::fromStdString(featureName)
                );
        auto* dvp = dynamic_cast<TechDraw::DrawViewPart*>(
            Gui::Command::runDocumentObjectCommand(
                Doc,
                *document,
                partViewFactory.toUtf8(),
                TechDraw::DrawProjGroupItem::getClassTypeId()
            )
        );
        if (!dvp) {
            throw Base::TypeError("CmdTechDrawView DVP not found");
        }
        dvp->translateLabel(
            "DrawProjGroupItem",
            "View",
            dvp->getNameInDocument()
        );
        const std::string viewCommand = Gui::Command::getObjectCmd(dvp);
        doCommand(Doc, "%s.addView(%s)", pageCommand.c_str(), viewCommand.c_str());
        dvp->Source.setValues(shapes);
        dvp->XSource.setValues(xShapes);

        ScopedDocumentStatus skipRecompute(*document, App::Document::Status::SkipRecompute, true);
        std::pair<Base::Vector3d, Base::Vector3d> dirs = !faceName.empty()
            ? DrawGuiUtil::getProjDirFromFace(partObj, faceName)
            : viewDirection();
        Base::Vector3d checkedDir = checkDirectionVsBasis(dirs.first);
        doCommand(
            Doc,
            "%s.Direction = FreeCAD.Vector(%.12f, %.12f, %.12f)",
            viewCommand.c_str(),
            checkedDir.x,
            checkedDir.y,
            checkedDir.z
        );
        doCommand(
            Doc,
            "%s.RotationVector = "
            "FreeCAD.Vector(%.12f, %.12f, %.12f)",
            viewCommand.c_str(),
            dirs.second.x,
            dirs.second.y,
            dirs.second.z
        );
        doCommand(
            Doc,
            "%s.XDirection = "
            "FreeCAD.Vector(%.12f, %.12f, %.12f)",
            viewCommand.c_str(),
            dirs.second.x,
            dirs.second.y,
            dirs.second.z
        );

        skipRecompute.restore();
        doCommand(Doc, "%s.recompute()", viewCommand.c_str());
        if (dvp->isError()) {
            throw Base::RuntimeError("The drawing view could not produce a valid result");
        }
        createdViews.push_back(dvp);
        CommandHelpers::groupTimelineOutputs(
            document,
            createdViews,
            "DrawingViews",
            QT_TRANSLATE_NOOP("Command", "Drawing Views")
        );
        // The task panel owns this exact creation transaction. Accept
        // commits the new view and its final projection settings as one undo
        // step; Cancel removes the provisional result atomically.
        TaskInternal::showDocumentDialog(new TaskDlgProjGroup(dvp, true), document);
    }
    catch (...) {
        abortCommand();
        throw;
    }
}

bool CmdTechDrawView::isActive() { return DrawGuiUtil::needPage(this); }

//===========================================================================
// TechDraw_BrokenView
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawBrokenView)

CmdTechDrawBrokenView::CmdTechDrawBrokenView()
  : Command("TechDraw_BrokenView")
{
    sAppModule      = "TechDraw";
    sGroup          = QT_TR_NOOP("TechDraw");
    sMenuText       = QT_TR_NOOP("Broken View");
    sToolTipText    = QT_TR_NOOP("Inserts a new broken view for the selected objects or base view and break definition objects");
    sWhatsThis      = "TechDraw_BrokenView";
    sStatusTip      = sToolTipText;
    sPixmap         = "actions/TechDraw_BrokenView";
}

void CmdTechDrawBrokenView::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    TechDraw::DrawPage* page = DrawGuiUtil::findPage(this);
    if (!page) {
        return;
    }
    App::Document* document = page->getDocument();
    if (!document) {
        return;
    }

    // get shape objects from a base view
    std::vector<App::DocumentObject*> shapesFromBase;
    std::vector<App::DocumentObject*> xShapesFromBase;
    std::vector<App::DocumentObject*> baseViews =
        getSelection().getObjectsOfType(TechDraw::DrawViewPart::getClassTypeId());
    TechDraw::DrawViewPart* dvp{nullptr};
    if (!baseViews.empty()) {
        dvp = static_cast<TechDraw::DrawViewPart*>(*baseViews.begin());
        if (!dvp || dvp->getDocument() != document
            || dvp->findParentPage() != page) {
            QMessageBox::warning(
                Gui::getMainWindow(),
                QObject::tr("Wrong selection"),
                QObject::tr(
                    "The base view must belong to the active drawing page."
                )
            );
            return;
        }
        shapesFromBase = dvp->Source.getValues();
        xShapesFromBase = dvp->XSource.getValues();
    }

    auto* doc = document;

    // get the shape objects from the selection
    std::vector<App::DocumentObject*> shapes;
    std::vector<App::DocumentObject*> xShapes;
    App::DocumentObject* faceObj = nullptr;
    std::string faceName;
    getSelectedShapes(this, shapes, xShapes, faceObj, faceName);

    // we need either a base view (dvp) or some shape objects in the selection
    if (!dvp && (shapes.empty() && xShapes.empty())) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Empty Selection"),
            QObject::tr("Select objects to break or a base view and break definition objects"));
        return;
    }

    shapes.insert(shapes.end(), shapesFromBase.begin(), shapesFromBase.end());
    xShapes.insert(xShapes.end(), xShapesFromBase.begin(), xShapesFromBase.end());


    // pick the Break objects out of the selected pile
    std::vector<Gui::SelectionObject> selection = getSelection().getSelectionEx(
        nullptr, App::DocumentObject::getClassTypeId(), Gui::ResolveMode::NoResolve);

    std::vector<App::DocumentObject*> breakObjects;
    for (auto& selObj : selection) {
        auto temp = selObj.getObject();
        if (!temp || temp->getDocument() != doc) {
            continue;
        }
        // a sketch outside a body is returned as an independent object in the selection
        if (selObj.getSubNames().empty()) {
            if (DrawBrokenView::isBreakObject(*temp)) {
                breakObjects.push_back(selObj.getObject());
            }
            continue;
        }
        // a sketch inside a body is returned as body + subelement, so we have to search through
        // subnames to find it.  This may(?) apply to App::Part and Group also?
        const auto& subname = selObj.getSubNames().front();
        if (!subname.empty() && subname.back() == '.') {
            auto* breakObject =
                temp->getSubObject(subname.c_str());
            if (breakObject
                && breakObject->getDocument() == doc
                && doc->containsObject(breakObject)
                && DrawBrokenView::isBreakObject(
                    *breakObject
                )) {
                breakObjects.push_back(breakObject);
            }
        }
    }
    if (breakObjects.empty()) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Wrong selection"),
            QObject::tr("No break objects found in this selection"));
        return;
    }

    // remove Break objects from shape pile
    shapes = DrawBrokenView::removeBreakObjects(breakObjects, shapes);
    xShapes = DrawBrokenView::removeBreakObjects(breakObjects, xShapes);
    if (shapes.empty() &&
        xShapes.empty()) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Wrong selection"),
            QObject::tr("No shapes, groups, or links in this selection"));
        return;
    }

    Gui::WaitCursor wc;
    try {
        TaskInternal::OwnedDocumentTransaction transaction(
            document,
            QT_TRANSLATE_NOOP("Command", "Create broken view")
        );
        ScopedDocumentStatus skipRecompute(
            *document,
            App::Document::Status::SkipRecompute,
            true
        );
        const std::string featureName =
            document->getUniqueObjectName("BrokenView");
        const std::string documentName =
            Base::InterpreterSingleton::strToPython(
                document->getName()
            );
        const QString brokenViewFactory =
            QStringLiteral(
                "App.getDocument('%1').addObject"
                "('TechDraw::DrawBrokenView', '%2')"
            )
                .arg(
                    QString::fromStdString(documentName),
                    QString::fromStdString(featureName)
                );
        auto* dbv = dynamic_cast<TechDraw::DrawBrokenView*>(
            Gui::Command::runDocumentObjectCommand(
                Doc,
                *document,
                brokenViewFactory.toUtf8(),
                TechDraw::DrawBrokenView::getClassTypeId()
            )
        );
        if (!dbv) {
            throw Base::TypeError(
                "The broken drawing view could not be created"
            );
        }
        dbv->translateLabel(
            "DrawBrokenView",
            "BrokenView",
            dbv->getNameInDocument()
        );
        const std::string pageCommand =
            Gui::Command::getObjectCmd(page);
        const std::string viewCommand =
            Gui::Command::getObjectCmd(dbv);
        doCommand(
            Doc,
            "%s.addView(%s)",
            pageCommand.c_str(),
            viewCommand.c_str()
        );
        dbv->Source.setValues(shapes);
        dbv->XSource.setValues(xShapes);
        dbv->Breaks.setValues(breakObjects);

        std::pair<Base::Vector3d, Base::Vector3d> dirs =
            !faceName.empty() && faceObj
            ? DrawGuiUtil::getProjDirFromFace(faceObj, faceName)
            : DrawGuiUtil::get3DDirAndRot();

        Base::Vector3d projDir =
            checkDirectionVsBasis(dirs.first);
        doCommand(
            Doc,
            "%s.Direction = FreeCAD.Vector(%.6f, %.6f, %.6f)",
            viewCommand.c_str(),
            projDir.x,
            projDir.y,
            projDir.z
        );
        doCommand(
            Doc,
            "%s.XDirection = FreeCAD.Vector(%.6f, %.6f, %.6f)",
            viewCommand.c_str(),
            dirs.second.x,
            dirs.second.y,
            dirs.second.z
        );
        skipRecompute.restore();
        dbv->recomputeFeature();
        if (dbv->isError()) {
            throw Base::RuntimeError(
                "The broken drawing view could not produce a valid result"
            );
        }
        TaskInternal::updateExactDocument(document);
        transaction.commit();
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Create broken view"),
            QString::fromUtf8(error.what())
        );
    }
}

bool CmdTechDrawBrokenView::isActive(void)
{
    return DrawGuiUtil::needPage(this);
}


//===========================================================================
// TechDraw_ActiveView
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawActiveView)

CmdTechDrawActiveView::CmdTechDrawActiveView() : Command("TechDraw_ActiveView")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Active View");
    sToolTipText = "Inserts an image of the open 3D view in the current page.\n"
               "If multiple 3D views are open, a selection dialog will be shown.";
    sWhatsThis = "TechDraw_ActiveView";
    sStatusTip = sToolTipText;
    sPixmap = "actions/TechDraw_ActiveView";
}

void CmdTechDrawActiveView::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    TechDraw::DrawPage* page = DrawGuiUtil::findPage(this, true);
    if (!page) {
        return;
    }
    App::Document* document = page->getDocument();
    if (!document
        || document->getBookedTransactionID()
            != App::NullTransaction) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Task in progress"),
            QObject::tr(
                "Finish the current operation before inserting an active view."
            )
        );
        return;
    }
    const int transactionId = openCommand(
        document,
        QT_TRANSLATE_NOOP("Command", "Create ActiveView")
    );
    if (transactionId == App::NullTransaction) {
        return;
    }
    try {
        TaskInternal::showDocumentDialog(
            new TaskDlgActiveView(page),
            document
        );
    }
    catch (...) {
        Gui::Command::abortCommand(transactionId);
        throw;
    }
}

bool CmdTechDrawActiveView::isActive() { return DrawGuiUtil::needPage(this, true); }

//===========================================================================
// TechDraw_SectionGroup
//===========================================================================

DEF_STD_CMD_ACL(CmdTechDrawSectionGroup)

CmdTechDrawSectionGroup::CmdTechDrawSectionGroup() : Command("TechDraw_SectionGroup")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Section View (Simple or Complex)");
    sToolTipText = QT_TR_NOOP("Inserts a simple or complex section view in the current page");
    sWhatsThis = "TechDraw_SectionGroup";
    sStatusTip = sToolTipText;
}

void CmdTechDrawSectionGroup::activated(int iMsg)
{
    //    Base::Console().message("CMD::SectionGrp - activated(%d)\n", iMsg);
    Gui::TaskView::TaskDialog* dlg = Gui::Control().activeDialog();
    if (dlg) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Task in progress"),
                             QObject::tr("Close active task dialog and try again"));
        return;
    }

    Gui::ActionGroup* pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    pcAction->setIcon(pcAction->actions().at(iMsg)->icon());
    switch (iMsg) {
        case 0:
            execSimpleSection(this);
            break;
        case 1:
            execComplexSection(this);
            break;
        default:
            Base::Console().message("CMD::SectionGrp - invalid iMsg: %d\n", iMsg);
    };
}

Gui::Action* CmdTechDrawSectionGroup::createAction()
{
    Gui::ActionGroup* pcAction = new Gui::ActionGroup(this, Gui::getMainWindow());
    pcAction->setDropDownMenu(true);
    applyCommandData(this->className(), pcAction);

    QAction* p1 = pcAction->addAction(QString());
    p1->setIcon(Gui::BitmapFactory().iconFromTheme("actions/TechDraw_SectionView"));
    p1->setObjectName(QStringLiteral("TechDraw_SectionView"));
    p1->setWhatsThis(QStringLiteral("TechDraw_SectionView"));
    QAction* p2 = pcAction->addAction(QString());
    p2->setIcon(Gui::BitmapFactory().iconFromTheme("actions/TechDraw_ComplexSection"));
    p2->setObjectName(QStringLiteral("TechDraw_ComplexSection"));
    p2->setWhatsThis(QStringLiteral("TechDraw_ComplexSection"));

    _pcAction = pcAction;
    languageChange();

    pcAction->setIcon(p1->icon());
    int defaultId = 0;
    pcAction->setProperty("defaultAction", QVariant(defaultId));

    return pcAction;
}

void CmdTechDrawSectionGroup::languageChange()
{
    Command::languageChange();

    if (!_pcAction)
        return;
    Gui::ActionGroup* pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    QList<QAction*> a = pcAction->actions();

    QAction* arc1 = a[0];
    arc1->setText(QApplication::translate("CmdTechDrawSectionGroup", "Section View"));
    arc1->setToolTip(QApplication::translate("TechDraw_SectionView", "Inserts a simple section view"));
    arc1->setStatusTip(arc1->toolTip());
    QAction* arc2 = a[1];
    arc2->setText(QApplication::translate("CmdTechDrawSectionGroup", "Complex Section View"));
    arc2->setToolTip(
        QApplication::translate("TechDraw_ComplexSection", "Inserts a complex section view"));
    arc2->setStatusTip(arc2->toolTip());
}

bool CmdTechDrawSectionGroup::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this, false);
    return (havePage && haveView);
}

//===========================================================================
// TechDraw_SectionView
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawSectionView)

CmdTechDrawSectionView::CmdTechDrawSectionView() : Command("TechDraw_SectionView")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Section View");
    sToolTipText = QT_TR_NOOP("Inserts a new section view based on the selected view in the current page");
    sWhatsThis = "TechDraw_SectionView";
    sStatusTip = sToolTipText;
    sPixmap = "actions/TechDraw_SectionView";
}

void CmdTechDrawSectionView::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    Gui::TaskView::TaskDialog* dlg = Gui::Control().activeDialog();
    if (dlg) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Task in progress"),
                             QObject::tr("Close active task dialog and try again"));
        return;
    }

    execSimpleSection(this);
}

bool CmdTechDrawSectionView::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this);
    bool taskInProgress = false;
    if (havePage) {
        taskInProgress = Gui::Control().activeDialog();
    }
    return (havePage && haveView && !taskInProgress);
}

void execSimpleSection(Gui::Command* cmd)
{
    std::vector<App::DocumentObject*> baseObj =
        cmd->getSelection().getObjectsOfType(TechDraw::DrawViewPart::getClassTypeId());
    if (baseObj.empty()) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Wrong selection"),
                             QObject::tr("Select at least 1 DrawViewPart object as base"));
        return;
    }

    TechDraw::DrawPage* page = DrawGuiUtil::findPage(cmd);
    if (!page) {
        return;
    }

    TechDraw::DrawViewPart* dvp = static_cast<TechDraw::DrawViewPart*>(*baseObj.begin());
    App::Document* document = page->getDocument();
    if (!dvp || dvp->getDocument() != document
        || dvp->findParentPage() != page) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Wrong selection"),
            QObject::tr(
                "The base view must belong to the active drawing page."
            )
        );
        return;
    }
    if (document->getBookedTransactionID()
        != App::NullTransaction) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Task in progress"),
            QObject::tr(
                "Finish the current operation before creating a section."
            )
        );
        return;
    }
    const int transactionId = cmd->openCommand(
        document,
        QT_TRANSLATE_NOOP("Command", "Create Section View")
    );
    if (transactionId == App::NullTransaction) {
        return;
    }
    try {
        TaskInternal::showDocumentDialog(
            new TaskDlgSectionView(dvp),
            document
        );
        TaskInternal::updateExactDocument(document);
    }
    catch (...) {
        Gui::Command::abortCommand(transactionId);
        throw;
    }
}

//===========================================================================
// TechDraw_ComplexSection
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawComplexSection)

CmdTechDrawComplexSection::CmdTechDrawComplexSection() : Command("TechDraw_ComplexSection")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Complex Section View");
    sToolTipText = QT_TR_NOOP("Inserts a complex section view based on the selected view in the current page");
    sWhatsThis = "TechDraw_ComplexSection";
    sStatusTip = sToolTipText;
    sPixmap = "actions/TechDraw_ComplexSection";
}

void CmdTechDrawComplexSection::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    Gui::TaskView::TaskDialog* dlg = Gui::Control().activeDialog();
    if (dlg) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Task in progress"),
                             QObject::tr("Close active task dialog and try again"));
        return;
    }

    execComplexSection(this);
}

bool CmdTechDrawComplexSection::isActive() { return DrawGuiUtil::needPage(this); }

//Complex Sections can be created without a baseView, so the gathering of input
//for the dialog is more involved that simple section
void execComplexSection(Gui::Command* cmd)
{
    TechDraw::DrawViewPart* baseView(nullptr);
    std::vector<App::DocumentObject*> shapes;
    std::vector<App::DocumentObject*> xShapes;
    App::DocumentObject* profileObject(nullptr);
    std::vector<std::string> profileSubs;
    Gui::ResolveMode resolve = Gui::ResolveMode::OldStyleElement;
    bool single = false;
    auto selection = cmd->getSelection().getSelectionEx(
        nullptr, App::DocumentObject::getClassTypeId(), resolve, single);
    for (auto& sel : selection) {
        bool is_linked = false;
        auto obj = sel.getObject();
        if (!obj) {
            continue;
        }
        if (obj->isDerivedFrom<TechDraw::DrawPage>()) {
            continue;
        }
        if (obj->isDerivedFrom<TechDraw::DrawViewPart>()) {
            //use the dvp's Sources as sources for this ComplexSection &
            //check the subelement(s) to see if they can be used as a profile
            baseView = static_cast<TechDraw::DrawViewPart*>(obj);
            if (!sel.getSubNames().empty()) {
                //need to add profile subs as parameter
                profileObject = baseView;
                profileSubs = sel.getSubNames();
            }
            continue;
        }
        if (obj->isDerivedFrom<App::LinkElement>()
            || obj->isDerivedFrom<App::LinkGroup>()
            || obj->isDerivedFrom<App::Link>()) {
            is_linked = true;
        }
        // If parent of the obj is a link to another document, we possibly need to treat non-link obj as linked, too
        // 1st, is obj in another document?
        if (obj->getDocument() != cmd->getDocument()) {
            std::set<App::DocumentObject*> parents = obj->getInListEx(true);
            for (auto& parent : parents) {
                // Only consider parents in the current document, i.e. possible links in this View's document
                if (parent->getDocument() != cmd->getDocument()) {
                    continue;
                }
                // 2nd, do we really have a link to obj?
                if (parent->isDerivedFrom<App::LinkElement>()
                    || parent->isDerivedFrom<App::LinkGroup>()
                    || parent->isDerivedFrom<App::Link>()) {
                    // We have a link chain from this document to obj, and obj is in another document -> it is an XLink target
                    is_linked = true;
                }
            }
        }
        if (is_linked) {
            xShapes.push_back(obj);
            continue;
        }
        //not a Link and not null.  assume to be drawable.  Undrawables will be
        // skipped later.
        if (TechDraw::DrawComplexSection::isProfileObject(obj)) {
            profileObject = obj;
        }
        else {
            shapes.push_back(obj);
        }
    }

    if (!baseView) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Wrong selection"),
                             QObject::tr("No base view selected"));
        return;
    }

    if (shapes.empty() && xShapes.empty() && !baseView) {
        QMessageBox::warning(
            Gui::getMainWindow(), QObject::tr("Wrong selection"),
            QObject::tr("No base view, shapes, groups, or links in this selection"));
        return;
    }
    if (!profileObject) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Wrong selection"),
                             QObject::tr("No profile object found in selection"));
        return;
    }

    TechDraw::DrawPage* page = DrawGuiUtil::findPage(cmd);
    if (!page) {
        return;
    }
    if (baseView->getDocument() != page->getDocument()
        || baseView->findParentPage() != page) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Wrong selection"),
            QObject::tr(
                "Select a base view from the drawing page being edited."
            )
        );
        return;
    }
    if (page->getDocument()->getBookedTransactionID()
        != App::NullTransaction) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Task in progress"),
            QObject::tr(
                "Finish the current operation before creating a complex "
                "section."
            )
        );
        return;
    }

    const int transactionId = cmd->openCommand(
        page->getDocument(),
        QT_TRANSLATE_NOOP("Command", "Create Complex Section")
    );
    if (transactionId == App::NullTransaction) {
        return;
    }
    try {
        TaskInternal::showDocumentDialog(
            new TaskDlgComplexSection(
                page,
                baseView,
                shapes,
                xShapes,
                profileObject,
                profileSubs
            ),
            page->getDocument()
        );
    }
    catch (...) {
        Gui::Command::abortCommand(transactionId);
        throw;
    }
}

//===========================================================================
// TechDraw_DetailView
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawDetailView)

CmdTechDrawDetailView::CmdTechDrawDetailView() : Command("TechDraw_DetailView")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Detail View");
    sToolTipText = QT_TR_NOOP("Inserts a new detail view based on the selected view in the current page");
    sWhatsThis = "TechDraw_DetailView";
    sStatusTip = sToolTipText;
    sPixmap = "actions/TechDraw_DetailView";
}

void CmdTechDrawDetailView::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    Gui::TaskView::TaskDialog* dialog =
        Gui::Control().activeDialog();
    if (dialog) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Task in progress"),
            QObject::tr("Close the active task and try again.")
        );
        return;
    }

    std::vector<App::DocumentObject*> baseObj =
        getSelection().getObjectsOfType(TechDraw::DrawViewPart::getClassTypeId());
    if (baseObj.empty()) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Wrong selection"),
                             QObject::tr("Select at least 1 DrawViewPart object as base"));
        return;
    }
    TechDraw::DrawViewPart* dvp = static_cast<TechDraw::DrawViewPart*>(*(baseObj.begin()));
    TechDraw::DrawPage* page = DrawGuiUtil::findPage(this);
    App::Document* document = page ? page->getDocument() : nullptr;
    if (!dvp || !page || dvp->getDocument() != document
        || dvp->findParentPage() != page) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Wrong selection"),
            QObject::tr(
                "The base view must belong to the active drawing page."
            )
        );
        return;
    }
    if (document->getBookedTransactionID()
        != App::NullTransaction) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Task in progress"),
            QObject::tr(
                "Finish the current operation before creating a detail view."
            )
        );
        return;
    }
    const int transactionId = openCommand(
        document,
        QT_TRANSLATE_NOOP("Command", "Create Detail view")
    );
    if (transactionId == App::NullTransaction) {
        return;
    }
    try {
        TaskInternal::showDocumentDialog(
            new TaskDlgDetail(dvp),
            document
        );
    }
    catch (...) {
        Gui::Command::abortCommand(transactionId);
        throw;
    }
}

bool CmdTechDrawDetailView::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this);
    bool taskInProgress = false;
    if (havePage) {
        taskInProgress = Gui::Control().activeDialog();
    }
    return (havePage && haveView && !taskInProgress);
}

//===========================================================================
// TechDraw_ProjectionGroup
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawProjectionGroup)

CmdTechDrawProjectionGroup::CmdTechDrawProjectionGroup() : Command("TechDraw_ProjectionGroup")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Projection Group");
    sToolTipText = QT_TR_NOOP("Inserts multiple new linked views of the selected objects in the current page");
    sWhatsThis = "TechDraw_ProjectionGroup";
    sStatusTip = sToolTipText;
    sPixmap = "actions/TechDraw_ProjectionGroup";
}

void CmdTechDrawProjectionGroup::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    //set projection direction from selected Face
    //use first object with a face selected
    std::vector<App::DocumentObject*> shapes;
    std::vector<App::DocumentObject*> xShapes;
    App::DocumentObject* partObj = nullptr;
    std::string faceName;
    Gui::ResolveMode resolve = Gui::ResolveMode::OldStyleElement;
    bool single = false;
    auto selection = getSelection().getSelectionEx(nullptr, App::DocumentObject::getClassTypeId(),
                                                   resolve, single);
    for (auto& sel : selection) {
        bool is_linked = false;
        auto obj = sel.getObject();
        if (!obj) {
            continue;
        }
        if (obj->isDerivedFrom<TechDraw::DrawPage>()) {
            continue;
        }
        if (obj->isDerivedFrom<App::LinkElement>()
            || obj->isDerivedFrom<App::LinkGroup>()
            || obj->isDerivedFrom<App::Link>()) {
            is_linked = true;
        }
        // If parent of the obj is a link to another document, we possibly need to treat non-link obj as linked, too
        // 1st, is obj in another document?
        if (obj->getDocument() != this->getDocument()) {
            std::set<App::DocumentObject*> parents = obj->getInListEx(true);
            for (auto& parent : parents) {
                // Only consider parents in the current document, i.e. possible links in this View's document
                if (parent->getDocument() != this->getDocument()) {
                    continue;
                }
                // 2nd, do we really have a link to obj?
                if (parent->isDerivedFrom<App::LinkElement>()
                    || parent->isDerivedFrom<App::LinkGroup>()
                    || parent->isDerivedFrom<App::Link>()) {
                    // We have a link chain from this document to obj, and obj is in another document -> it is an XLink target
                    is_linked = true;
                }
            }
        }
        if (is_linked) {
            xShapes.push_back(obj);
            continue;
        }
        //not a Link and not null.  assume to be drawable.  Undrawables will be
        // skipped later.
        shapes.push_back(obj);
        if (partObj) {
            continue;
        }
        for (auto& sub : sel.getSubNames()) {
            if (TechDraw::DrawUtil::getGeomTypeFromName(sub) == "Face") {
                faceName = sub;
                partObj = obj;
                break;
            }
        }
    }
    if (shapes.empty() && xShapes.empty()) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Wrong selection"),
                             QObject::tr("No shapes, groups, or links in this selection"));
        return;
    }

    TechDraw::DrawPage* page = DrawGuiUtil::findPage(this);
    if (!page) {
        return;
    }
    auto* document = page->getDocument();
    if (!document
        || document->getBookedTransactionID()
            != App::NullTransaction) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Task in progress"),
            QObject::tr(
                "Finish the current operation before creating a projection "
                "group."
            )
        );
        return;
    }

    Gui::WaitCursor wc;

    const int transactionId = openCommand(
        document,
        QT_TRANSLATE_NOOP("Command", "Create projection group")
    );
    if (transactionId == App::NullTransaction) {
        return;
    }
    try {
        std::string multiViewName =
            document->getUniqueObjectName("ProjGroup");
        const std::string documentName =
            Base::InterpreterSingleton::strToPython(
                document->getName()
            );
        const QString projectionGroupFactory =
            QStringLiteral(
                "App.getDocument('%1').addObject"
                "('TechDraw::DrawProjGroup', '%2')"
            )
                .arg(
                    QString::fromStdString(documentName),
                    QString::fromStdString(multiViewName)
                );
        auto* multiView =
            dynamic_cast<TechDraw::DrawProjGroup*>(
                Gui::Command::runDocumentObjectCommand(
                    Doc,
                    *document,
                    projectionGroupFactory.toUtf8(),
                    TechDraw::DrawProjGroup::getClassTypeId()
                )
            );
        if (!multiView) {
            throw Base::TypeError(
                "CmdTechDrawProjectionGroup projection group not found"
            );
        }
        const std::string pageCommand =
            Gui::Command::getObjectCmd(page);
        const std::string groupCommand =
            Gui::Command::getObjectCmd(multiView);
        doCommand(
            Doc,
            "%s.addView(%s)",
            pageCommand.c_str(),
            groupCommand.c_str()
        );
        multiView->Source.setValues(shapes);
        multiView->XSource.setValues(xShapes);
        auto* anchor =
            dynamic_cast<TechDraw::DrawProjGroupItem*>(
                Gui::Command::runDocumentObjectCommand(
                    Doc,
                    *document,
                    QStringLiteral("%1.addProjection('Front')")
                        .arg(QString::fromStdString(groupCommand))
                        .toUtf8(),
                    TechDraw::DrawProjGroupItem::getClassTypeId()
                )
            );
        if (!anchor || anchor->getPGroup() != multiView) {
            throw Base::RuntimeError(
                "The projection group factory returned an invalid anchor"
            );
        }

        std::pair<Base::Vector3d, Base::Vector3d> dirs =
            !faceName.empty()
            ? DrawGuiUtil::getProjDirFromFace(partObj, faceName)
            : DrawGuiUtil::get3DDirAndRot();

        Base::Vector3d checkedDir =
            checkDirectionVsBasis(dirs.first);
        ScopedDocumentStatus skipRecompute(
            *document,
            App::Document::Status::SkipRecompute,
            true
        );
        doCommand(
            Doc,
            "%s.Anchor.Direction = "
            "FreeCAD.Vector(%.12f, %.12f, %.12f)",
            groupCommand.c_str(),
            checkedDir.x,
            checkedDir.y,
            checkedDir.z
        );
        doCommand(
            Doc,
            "%s.Anchor.RotationVector = "
            "FreeCAD.Vector(%.12f, %.12f, %.12f)",
            groupCommand.c_str(),
            dirs.second.x,
            dirs.second.y,
            dirs.second.z
        );
        doCommand(
            Doc,
            "%s.Anchor.XDirection = "
            "FreeCAD.Vector(%.12f, %.12f, %.12f)",
            groupCommand.c_str(),
            dirs.second.x,
            dirs.second.y,
            dirs.second.z
        );
        skipRecompute.restore();

        doCommand(
            Doc,
            "%s.Anchor.recompute()",
            groupCommand.c_str()
        );
        if (multiView->Anchor.getValue() != anchor
            || multiView->isError() || anchor->isError()) {
            throw Base::RuntimeError(
                "The projection group could not produce a valid anchor view"
            );
        }
        TaskInternal::updateExactDocument(document);

        // Keep the exact creation transaction open while the task
        // configures the linked views. The task boundary commits or aborts
        // it atomically.
        TaskInternal::showDocumentDialog(
            new TaskDlgProjGroup(multiView, true),
            document
        );
    }
    catch (...) {
        Gui::Command::abortCommand(transactionId);
        throw;
    }
}

bool CmdTechDrawProjectionGroup::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool taskInProgress = false;
    if (havePage) {
        taskInProgress = Gui::Control().activeDialog();
    }
    return (havePage && !taskInProgress);
}

//! common checks of Selection for Dimension commands
//non-empty selection, no more than maxObjs selected and at least 1 DrawingPage exists
bool _checkSelectionBalloon(Gui::Command* cmd, unsigned maxObjs)
{
    std::vector<Gui::SelectionObject> selection = cmd->getSelection().getSelectionEx();
    if (selection.empty()) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Incorrect Selection"),
                             QObject::tr("Select an object first"));
        return false;
    }

    const std::vector<std::string> SubNames = selection[0].getSubNames();
    if (SubNames.size() > maxObjs) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Incorrect Selection"),
                             QObject::tr("Too many objects selected"));
        return false;
    }

    std::vector<App::DocumentObject*> pages =
        cmd->getDocument()->getObjectsOfType(TechDraw::DrawPage::getClassTypeId());
    if (pages.empty()) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Incorrect Selection"),
                             QObject::tr("Create a page first"));
        return false;
    }
    return true;
}

bool _checkDrawViewPartBalloon(Gui::Command* cmd)
{
    std::vector<Gui::SelectionObject> selection = cmd->getSelection().getSelectionEx();
    if (selection.empty()) {
        return false;
    }
    auto objFeat(dynamic_cast<TechDraw::DrawViewPart*>(selection[0].getObject()));
    if (!objFeat) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Incorrect Selection"),
                             QObject::tr("No view of a part in selection"));
        return false;
    }
    return true;
}

bool _checkDirectPlacement(const QGIView* view, const std::vector<std::string>& subNames,
                           QPointF& placement)
{
    // Let's see, if we can help speed up the placement of the balloon:
    // As of now we support:
    //     Single selected vertex: place the balloon tip end here
    //     Single selected edge:   place the balloon tip at its midpoint (suggested placement for e.g. chamfer dimensions)
    //
    // Single selected faces are currently not supported, but maybe we could in this case use the center of mass?

    if (subNames.size() != 1) {
        // If nothing or more than one subjects are selected, let the user decide, where to place the balloon
        return false;
    }

    const QGIViewPart* viewPart = dynamic_cast<const QGIViewPart*>(view);
    if (!viewPart) {
        //not a view of a part, so no geometry to attach to
        return false;
    }

    std::string geoType = TechDraw::DrawUtil::getGeomTypeFromName(subNames[0]);
    if (geoType == "Vertex") {
        int index = TechDraw::DrawUtil::getIndexFromName(subNames[0]);
        TechDraw::VertexPtr vertex =
            static_cast<DrawViewPart*>(viewPart->getViewObject())->getProjVertexByIndex(index);
        if (vertex) {
            placement = viewPart->mapToScene(Rez::guiX(vertex->x()), Rez::guiX(vertex->y()));
            return true;
        }
    }
    else if (geoType == "Edge") {
        int index = TechDraw::DrawUtil::getIndexFromName(subNames[0]);
        TechDraw::BaseGeomPtr geo =
            static_cast<DrawViewPart*>(viewPart->getViewObject())->getGeomByIndex(index);
        if (geo) {
            Base::Vector3d midPoint(Rez::guiX(geo->getMidPoint()));
            placement = viewPart->mapToScene(midPoint.x, midPoint.y);
            return true;
        }
    }

    return false;
}

DEF_STD_CMD_A(CmdTechDrawBalloon)

CmdTechDrawBalloon::CmdTechDrawBalloon() : Command("TechDraw_Balloon")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Balloon Annotation");
    sToolTipText = QT_TR_NOOP("Inserts a new balloon annotation in the selected view");
    sWhatsThis = "TechDraw_Balloon";
    sStatusTip = sToolTipText;
    sPixmap = "TechDraw_Balloon";
}

void CmdTechDrawBalloon::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    bool result = _checkSelectionBalloon(this, 1);
    if (!result) {
        return;
    }

    std::vector<Gui::SelectionObject> selection = getSelection().getSelectionEx();

    auto objFeat(dynamic_cast<TechDraw::DrawView*>(selection[0].getObject()));
    if (!objFeat) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Incorrect Selection"),
            QObject::tr("Select a drawing view for the balloon.")
        );
        return;
    }

    TechDraw::DrawPage* page = objFeat->findParentPage();
    TechDraw::DrawPage* activePage = DrawGuiUtil::findPage(this);
    if (!page || page != activePage
        || objFeat->getDocument() != page->getDocument()) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Incorrect Selection"),
            QObject::tr(
                "The selected view must belong to the active drawing page."
            )
        );
        return;
    }

    Gui::Document* guiDoc =
        Gui::Application::Instance->getDocument(
            page->getDocument()
        );
    if (!guiDoc) {
        return;
    }
    ViewProviderPage* pageVP = freecad_cast<ViewProviderPage*>(guiDoc->getViewProvider(page));
    ViewProviderDrawingView* viewVP =
        freecad_cast<ViewProviderDrawingView*>(guiDoc->getViewProvider(objFeat));

    if (!pageVP || !viewVP) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Create balloon"),
            QObject::tr(
                "The selected drawing view is not available on the page."
            )
        );
        return;
    }

    QGVPage* viewPage = pageVP->getQGVPage();
    QGSPage* scenePage = pageVP->getQGSPage();
    if (!viewPage || !scenePage) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Create balloon"),
            QObject::tr("The drawing page is not ready for placement.")
        );
        return;
    }

    auto* view = dynamic_cast<QGIView*>(viewVP->getQView());
    QPointF placement;
    const auto subNames = selection[0].getSubNames();
    if (view
        && _checkDirectPlacement(
            view,
            subNames,
            placement
        )) {
        scenePage->createBalloon(
            placement,
            objFeat,
            subNames.front());
        return;
    }
    viewPage->startBalloonPlacing(objFeat);
}

bool CmdTechDrawBalloon::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this, false);
    bool taskInProgress = Gui::Control().activeDialog();
    return (havePage && haveView && !taskInProgress);
}

//===========================================================================
// TechDraw_ClipGroup
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawClipGroup)

CmdTechDrawClipGroup::CmdTechDrawClipGroup() : Command("TechDraw_ClipGroup")
{
    // setting the
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Clip Group");
    sToolTipText = QT_TR_NOOP("Inserts a new clip group for the selected view");
    sWhatsThis = "TechDraw_ClipGroup";
    sStatusTip = sToolTipText;
    sPixmap = "actions/TechDraw_ClipGroup";
}

void CmdTechDrawClipGroup::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    TechDraw::DrawPage* page = DrawGuiUtil::findPage(this);
    if (!page) {
        return;
    }
    App::Document* document = page->getDocument();
    try {
        TaskInternal::OwnedDocumentTransaction transaction(
            document,
            QT_TRANSLATE_NOOP("Command", "Create clip")
        );
        const std::string featureName =
            document->getUniqueObjectName("Clip");
        const std::string documentName =
            Base::InterpreterSingleton::strToPython(
                document->getName()
            );
        const QString clipFactory =
            QStringLiteral(
                "App.getDocument('%1').addObject"
                "('TechDraw::DrawViewClip', '%2')"
            )
                .arg(
                    QString::fromStdString(documentName),
                    QString::fromStdString(featureName)
                );
        auto* clip = dynamic_cast<TechDraw::DrawViewClip*>(
            Gui::Command::runDocumentObjectCommand(
                Doc,
                *document,
                clipFactory.toUtf8(),
                TechDraw::DrawViewClip::getClassTypeId()
            )
        );
        if (!clip) {
            throw Base::RuntimeError(
                "The clip group could not be created"
            );
        }
        const std::string pageCommand =
            Gui::Command::getObjectCmd(page);
        const std::string clipCommand =
            Gui::Command::getObjectCmd(clip);
        doCommand(
            Doc,
            "%s.addView(%s)",
            pageCommand.c_str(),
            clipCommand.c_str()
        );
        clip->recomputeFeature();
        if (clip->isError()) {
            throw Base::RuntimeError(
                "The clip group could not be generated"
            );
        }
        TaskInternal::updateExactDocument(document);
        transaction.commit();
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Create clip group"),
            QString::fromUtf8(error.what())
        );
    }
}

bool CmdTechDrawClipGroup::isActive() { return DrawGuiUtil::needPage(this); }

//===========================================================================
// TechDraw_ClipGroupAdd
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawClipGroupAdd)

CmdTechDrawClipGroupAdd::CmdTechDrawClipGroupAdd() : Command("TechDraw_ClipGroupAdd")
{
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Add View To Clip Group");
    sToolTipText = QT_TR_NOOP("Adds the selected view to a clip group");
    sWhatsThis = "TechDraw_ClipGroupAdd";
    sStatusTip = sToolTipText;
    sPixmap = "actions/TechDraw_ClipGroupAdd";
}

void CmdTechDrawClipGroupAdd::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    std::vector<Gui::SelectionObject> selection = getSelection().getSelectionEx();
    if (selection.size() != 2) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Wrong selection"),
                             QObject::tr("Select one clip group and one view"));
        return;
    }

    TechDraw::DrawViewClip* clip = nullptr;
    TechDraw::DrawView* view = nullptr;
    std::vector<Gui::SelectionObject>::iterator itSel = selection.begin();
    for (; itSel != selection.end(); itSel++) {
        auto* selectedObject = (*itSel).getObject();
        if (!selectedObject) {
            continue;
        }
        if (selectedObject->isDerivedFrom<TechDraw::DrawViewClip>()) {
            clip = static_cast<TechDraw::DrawViewClip*>(selectedObject);
        }
        else if (selectedObject->isDerivedFrom<TechDraw::DrawView>()) {
            view = static_cast<TechDraw::DrawView*>(selectedObject);
        }
    }
    if (!view) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Wrong selection"),
                             QObject::tr("Select exactly one view to add to clip group"));
        return;
    }
    if (!clip) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Wrong selection"),
                             QObject::tr("Select exactly one clip group"));
        return;
    }

    TechDraw::DrawPage* pageClip = clip->findParentPage();
    TechDraw::DrawPage* pageView = view->findParentPage();

    TechDraw::DrawPage* activePage = DrawGuiUtil::findPage(this);
    if (!pageClip || pageClip != pageView
        || pageClip != activePage
        || clip->getDocument() != view->getDocument()) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Wrong selection"),
                             QObject::tr(
                                 "Clip and view must belong to the active drawing page"
                             ));
        return;
    }

    if (clip->isViewInClip(view)) {
        QMessageBox::information(
            Gui::getMainWindow(),
            QObject::tr("Add to clip group"),
            QObject::tr("The selected view is already in this clip group.")
        );
        return;
    }

    App::Document* document = pageClip->getDocument();
    try {
        TaskInternal::OwnedDocumentTransaction transaction(
            document,
            QT_TRANSLATE_NOOP("Command", "Add to clip group")
        );
        const std::string clipCommand =
            Gui::Command::getObjectCmd(clip);
        const std::string viewCommand =
            Gui::Command::getObjectCmd(view);
        doCommand(
            Doc,
            "%s.addView(%s)",
            clipCommand.c_str(),
            viewCommand.c_str()
        );
        clip->recomputeFeature();
        if (clip->isError()) {
            throw Base::RuntimeError(
                "The view could not be added to the clip group"
            );
        }
        TaskInternal::updateExactDocument(document);
        transaction.commit();
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Add to clip group"),
            QString::fromUtf8(error.what())
        );
    }
}

bool CmdTechDrawClipGroupAdd::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveClip = false;
    if (havePage) {
        auto drawClipType(TechDraw::DrawViewClip::getClassTypeId());
        auto selClips = getDocument()->getObjectsOfType(drawClipType);
        if (!selClips.empty()) {
            haveClip = true;
        }
    }
    return (havePage && haveClip);
}

//===========================================================================
// TechDraw_ClipGroupRemove
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawClipGroupRemove)

CmdTechDrawClipGroupRemove::CmdTechDrawClipGroupRemove() : Command("TechDraw_ClipGroupRemove")
{
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Remove From Clip Group");
    sToolTipText = QT_TR_NOOP("Removes a view based on the selected clip group");
    sWhatsThis = "TechDraw_ClipGroupRemove";
    sStatusTip = sToolTipText;
    sPixmap = "actions/TechDraw_ClipGroupRemove";
}

void CmdTechDrawClipGroupRemove::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    auto dObj(getSelection().getObjectsOfType(TechDraw::DrawView::getClassTypeId()));
    if (dObj.empty()) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Wrong selection"),
                             QObject::tr("Select exactly one view to remove from clip group"));
        return;
    }

    if (dObj.size() != 1) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Wrong selection"),
            QObject::tr(
                "Select exactly one view to remove from a clip group"
            )
        );
        return;
    }
    auto* view = static_cast<TechDraw::DrawView*>(dObj.front());

    TechDraw::DrawPage* page = view->findParentPage();
    TechDraw::DrawPage* activePage = DrawGuiUtil::findPage(this);
    if (!page || page != activePage) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Wrong selection"),
            QObject::tr(
                "The selected view must belong to the active drawing page"
            )
        );
        return;
    }
    const std::vector<App::DocumentObject*> pViews =
        page->getAllActiveViews();
    TechDraw::DrawViewClip* clip(nullptr);
    for (auto& v : pViews) {
        clip = dynamic_cast<TechDraw::DrawViewClip*>(v);
        if (clip && clip->isViewInClip(view)) {
            break;
        }
        clip = nullptr;
    }

    if (!clip) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Wrong selection"),
                             QObject::tr("View does not belong to a clip"));
        return;
    }

    App::Document* document = page->getDocument();
    try {
        TaskInternal::OwnedDocumentTransaction transaction(
            document,
            QT_TRANSLATE_NOOP("Command", "Remove from clip group")
        );
        const std::string clipCommand =
            Gui::Command::getObjectCmd(clip);
        const std::string viewCommand =
            Gui::Command::getObjectCmd(view);
        doCommand(
            Doc,
            "%s.removeView(%s)",
            clipCommand.c_str(),
            viewCommand.c_str()
        );
        clip->recomputeFeature();
        if (clip->isError()) {
            throw Base::RuntimeError(
                "The view could not be removed from the clip group"
            );
        }
        TaskInternal::updateExactDocument(document);
        transaction.commit();
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Remove from clip group"),
            QString::fromUtf8(error.what())
        );
    }
}

bool CmdTechDrawClipGroupRemove::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveClip = false;
    if (havePage) {
        auto drawClipType(TechDraw::DrawViewClip::getClassTypeId());
        auto selClips = getDocument()->getObjectsOfType(drawClipType);
        if (!selClips.empty()) {
            haveClip = true;
        }
    }
    return (havePage && haveClip);
}


//===========================================================================
// TechDraw_Symbol
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawSymbol)

CmdTechDrawSymbol::CmdTechDrawSymbol() : Command("TechDraw_Symbol")
{
    // setting the Gui eye-candy
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Insert SVG");
    sToolTipText = QT_TR_NOOP("Inserts a symbol from an SVG file");
    sWhatsThis = "TechDraw_Symbol";
    sStatusTip = sToolTipText;
    sPixmap = "actions/TechDraw_Symbol";
}

void CmdTechDrawSymbol::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    TechDraw::DrawPage* page = DrawGuiUtil::findPage(this);
    if (!page) {
        return;
    }
    TaskInternal::ObjectIdentity<TechDraw::DrawPage>
        pageIdentity(page);
    App::Document* document = page->getDocument();
    auto* baseView = CommandHelpers::firstViewInSelection(this);
    const bool hadBaseView = baseView != nullptr;
    TaskInternal::ObjectIdentity<TechDraw::DrawView>
        baseViewIdentity(baseView);
    if (baseView
        && (baseView->getDocument() != document
            || baseView->findParentPage() != page)) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Wrong selection"),
            QObject::tr(
                "The symbol owner must belong to the active drawing page."
            )
        );
        return;
    }

    // Reading an image
    const Gui::FileDialog::FilterList filterList {
        {QStringLiteral("SVG"), {"*.svg", "*.svgz"}},
        Gui::FileDialog::Filter::AllFiles(),
    };
    QString filename = Gui::FileDialog::getOpenFileName(
        Gui::getMainWindow(), QObject::tr("Choose an SVG file to open"),
        Preferences::defaultSymbolDir(),
        filterList);

    if (filename.isEmpty()) {
        return;
    }

    page = pageIdentity.resolve();
    if (!page || page->getDocument() != document) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Create symbol"),
            QObject::tr("The target drawing page was closed.")
        );
        return;
    }
    baseView = baseViewIdentity.resolve();
    if (hadBaseView && !baseView) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Create symbol"),
            QObject::tr("The selected owner view was closed.")
        );
        return;
    }

    const std::string filespec = DU::cleanFilespecBackslash(
        Base::Tools::escapeEncodeFilename(filename.toStdString())
    );
    const std::string pythonFilespec =
        Base::InterpreterSingleton::strToPython(filespec);
    try {
        TaskInternal::OwnedDocumentTransaction transaction(
            document,
            QT_TRANSLATE_NOOP("Command", "Create Symbol")
        );
        const std::string featureName =
            document->getUniqueObjectName("Symbol");
        const std::string documentName =
            Base::InterpreterSingleton::strToPython(
                document->getName()
            );
        doCommand(Doc, "import codecs");
        doCommand(
            Doc,
            "f = codecs.open('%s', 'r', encoding='utf-8')",
            pythonFilespec.c_str()
        );
        doCommand(Doc, "svg = f.read()");
        doCommand(Doc, "f.close()");
        const QString symbolFactory =
            QStringLiteral(
                "App.getDocument('%1').addObject"
                "('TechDraw::DrawViewSymbol', '%2')"
            )
                .arg(
                    QString::fromStdString(documentName),
                    QString::fromStdString(featureName)
                );
        auto* symbol =
            dynamic_cast<TechDraw::DrawViewSymbol*>(
                Gui::Command::runDocumentObjectCommand(
                    Doc,
                    *document,
                    symbolFactory.toUtf8(),
                    TechDraw::DrawViewSymbol::getClassTypeId()
                )
            );
        if (!symbol) {
            throw Base::RuntimeError(
                "The drawing symbol could not be created"
            );
        }
        symbol->translateLabel(
            "DrawViewSymbol",
            "Symbol",
            symbol->getNameInDocument()
        );
        const std::string symbolCommand =
            Gui::Command::getObjectCmd(symbol);
        const std::string pageCommand =
            Gui::Command::getObjectCmd(page);
        doCommand(
            Doc,
            "%s.Symbol = svg",
            symbolCommand.c_str()
        );

        if (baseView) {
            const std::string baseCommand =
                Gui::Command::getObjectCmd(baseView);
            doCommand(
                Doc,
                "%s.Owner = %s",
                symbolCommand.c_str(),
                baseCommand.c_str()
            );
        }

        doCommand(
            Doc,
            "%s.addView(%s)",
            pageCommand.c_str(),
            symbolCommand.c_str()
        );
        symbol->recomputeFeature();
        if (symbol->isError()) {
            throw Base::RuntimeError(
                "The drawing symbol could not be generated"
            );
        }
        TaskInternal::updateExactDocument(document);
        transaction.commit();
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Create symbol"),
            QString::fromUtf8(error.what())
        );
    }
}

bool CmdTechDrawSymbol::isActive() { return DrawGuiUtil::needPage(this); }

//===========================================================================
// TechDraw_DraftView
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawDraftView)

CmdTechDrawDraftView::CmdTechDrawDraftView() : Command("TechDraw_DraftView")
{
    // setting the Gui eye-candy
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Draft View");
    //: "Draft" is a workbench and should not be translated
    sToolTipText = QT_TR_NOOP("Inserts a view of a Draft object");
    sWhatsThis = "TechDraw_NewDraft";
    sStatusTip = sToolTipText;
    sPixmap = "actions/TechDraw_DraftView";
}

void CmdTechDrawDraftView::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    std::vector<App::DocumentObject*> objects =
        getSelection().getObjectsOfType(App::DocumentObject::getClassTypeId());

    if (objects.empty()) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Wrong selection"),
                             QObject::tr("Select at least one object"));
        return;
    }

    TechDraw::DrawPage* page = DrawGuiUtil::findPage(this);
    if (!page) {
        return;
    }
    App::Document* document = page->getDocument();
    std::erase_if(
        objects,
        [document](const App::DocumentObject* object) {
            return !object
                || object->getDocument() != document
                || object->isDerivedFrom<TechDraw::DrawPage>()
                || object->isDerivedFrom<TechDraw::DrawView>();
        }
    );
    if (objects.empty()) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Wrong selection"),
            QObject::tr("Select at least one model object from the drawing document.")
        );
        return;
    }

    std::pair<Base::Vector3d, Base::Vector3d> dirs = DrawGuiUtil::get3DDirAndRot();
    Base::Vector3d checkedDir = checkDirectionVsBasis(dirs.first);
    try {
        TaskInternal::OwnedDocumentTransaction transaction(
            document,
            QT_TRANSLATE_NOOP("Command", "Create Draft views")
        );
        const std::string documentName = Base::InterpreterSingleton::strToPython(document->getName());
        const std::string pageCommand = Gui::Command::getObjectCmd(page);
        std::vector<App::DocumentObject*> createdViews;
        createdViews.reserve(objects.size());
        for (auto* object : objects) {
            const std::string featureName = document->getUniqueObjectName("DraftView");
            const QString draftViewFactory =
                QStringLiteral(
                    "App.getDocument('%1').addObject"
                    "('TechDraw::DrawViewDraft', '%2')"
                )
                    .arg(
                        QString::fromStdString(documentName),
                        QString::fromStdString(featureName)
                    );
            auto* draftView =
                dynamic_cast<TechDraw::DrawViewDraft*>(
                    Gui::Command::runDocumentObjectCommand(
                        Doc,
                        *document,
                        draftViewFactory.toUtf8(),
                        TechDraw::DrawViewDraft::getClassTypeId()
                    )
                );
            if (!draftView) {
                throw Base::RuntimeError("A Draft drawing view could not be created");
            }
            draftView->translateLabel(
                "DrawViewDraft",
                "DraftView",
                draftView->getNameInDocument()
            );
            const std::string draftCommand = Gui::Command::getObjectCmd(draftView);
            const std::string sourceCommand = Gui::Command::getObjectCmd(object);
            doCommand(Doc, "%s.Source = %s", draftCommand.c_str(), sourceCommand.c_str());
            doCommand(Doc, "%s.addView(%s)", pageCommand.c_str(), draftCommand.c_str());
            doCommand(
                Doc,
                "if %s.Scale: %s.Scale = %s.Scale",
                pageCommand.c_str(),
                draftCommand.c_str(),
                pageCommand.c_str()
            );
            doCommand(
                Doc,
                "%s.Direction = "
                "FreeCAD.Vector(%.12f, %.12f, %.12f)",
                draftCommand.c_str(),
                checkedDir.x,
                checkedDir.y,
                checkedDir.z
            );
            draftView->recomputeFeature();
            if (draftView->isError()) {
                throw Base::RuntimeError("A Draft drawing view could not be generated");
            }
            createdViews.push_back(draftView);
        }
        CommandHelpers::groupTimelineOutputs(
            document,
            createdViews,
            "DraftViews",
            QT_TRANSLATE_NOOP("Command", "Draft Views")
        );
        TaskInternal::updateExactDocument(document);
        transaction.commit();
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Create Draft views"),
            QString::fromUtf8(error.what())
        );
    }
}

bool CmdTechDrawDraftView::isActive()
{
    return DrawGuiUtil::needPage(this);
}

//===========================================================================
// TechDraw_SpreadsheetView
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawSpreadsheetView)

CmdTechDrawSpreadsheetView::CmdTechDrawSpreadsheetView() : Command("TechDraw_SpreadsheetView")
{
    // setting the
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Spreadsheet View");
    sToolTipText = QT_TR_NOOP("Inserts a view of a spreadsheet in the current page");
    sWhatsThis = "TechDraw_SpreadsheetView";
    sStatusTip = sToolTipText;
    sPixmap = "actions/TechDraw_SpreadsheetView";
}

void CmdTechDrawSpreadsheetView::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    TechDraw::DrawPage* page = DrawGuiUtil::findPage(this);
    if (!page) {
        return;
    }
    App::Document* document = page->getDocument();

    const std::vector<App::DocumentObject*> spreads =
        getSelection().getObjectsOfType(Spreadsheet::Sheet::getClassTypeId());
    if (spreads.size() != 1) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Wrong selection"),
                             QObject::tr("Select exactly one spreadsheet object"));
        return;
    }
    auto* spreadsheet =
        dynamic_cast<Spreadsheet::Sheet*>(spreads.front());
    if (!spreadsheet || spreadsheet->getDocument() != document) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Wrong selection"),
            QObject::tr(
                "The spreadsheet must belong to the drawing document."
            )
        );
        return;
    }

    // look for an owner view in the selection
    auto* baseView = CommandHelpers::firstViewInSelection(this);
    if (baseView
        && (baseView->getDocument() != document
            || baseView->findParentPage() != page)) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Wrong selection"),
            QObject::tr(
                "The spreadsheet owner must belong to the active drawing page."
            )
        );
        return;
    }

    try {
        TaskInternal::OwnedDocumentTransaction transaction(
            document,
            QT_TRANSLATE_NOOP("Command", "Create spreadsheet view")
        );
        const std::string documentName =
            Base::InterpreterSingleton::strToPython(
                document->getName()
            );
        const std::string featureName =
            document->getUniqueObjectName("Sheet");
        const QString spreadsheetViewFactory =
            QStringLiteral(
                "App.getDocument('%1').addObject"
                "('TechDraw::DrawViewSpreadsheet', '%2')"
            )
                .arg(
                    QString::fromStdString(documentName),
                    QString::fromStdString(featureName)
                );
        auto* sheetView =
            dynamic_cast<TechDraw::DrawViewSpreadsheet*>(
                Gui::Command::runDocumentObjectCommand(
                    Doc,
                    *document,
                    spreadsheetViewFactory.toUtf8(),
                    TechDraw::DrawViewSpreadsheet::getClassTypeId()
                )
            );
        if (!sheetView) {
            throw Base::RuntimeError(
                "The spreadsheet drawing view could not be created"
            );
        }
        sheetView->translateLabel(
            "DrawViewSpreadsheet",
            "Sheet",
            sheetView->getNameInDocument()
        );
        const std::string sheetCommand =
            Gui::Command::getObjectCmd(sheetView);
        const std::string sourceCommand =
            Gui::Command::getObjectCmd(spreadsheet);
        const std::string pageCommand =
            Gui::Command::getObjectCmd(page);
        doCommand(
            Doc,
            "%s.Source = %s",
            sheetCommand.c_str(),
            sourceCommand.c_str()
        );
        if (baseView) {
            const std::string baseCommand =
                Gui::Command::getObjectCmd(baseView);
            doCommand(
                Doc,
                "%s.Owner = %s",
                sheetCommand.c_str(),
                baseCommand.c_str()
            );
        }
        doCommand(
            Doc,
            "%s.addView(%s)",
            pageCommand.c_str(),
            sheetCommand.c_str()
        );
        doCommand(
            Doc,
            "if %s.Scale: %s.Scale = %s.Scale",
            pageCommand.c_str(),
            sheetCommand.c_str(),
            pageCommand.c_str()
        );
        sheetView->recomputeFeature();
        if (sheetView->isError()) {
            throw Base::RuntimeError(
                "The spreadsheet drawing view could not be generated"
            );
        }
        TaskInternal::updateExactDocument(document);
        transaction.commit();
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Create spreadsheet view"),
            QString::fromUtf8(error.what())
        );
    }
}

bool CmdTechDrawSpreadsheetView::isActive()
{
    //need a Page and a SpreadSheet::Sheet
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveSheet = false;
    if (havePage) {
        auto spreadSheetType(Spreadsheet::Sheet::getClassTypeId());
        auto selSheets = getDocument()->getObjectsOfType(spreadSheetType);
        if (!selSheets.empty()) {
            haveSheet = true;
        }
    }
    return (havePage && haveSheet);
}


//===========================================================================
// TechDraw_ExportPageSVG
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawExportPageSVG)

CmdTechDrawExportPageSVG::CmdTechDrawExportPageSVG() : Command("TechDraw_ExportPageSVG")
{
    sGroup = QT_TR_NOOP("File");
    sMenuText = QT_TR_NOOP("Export Page as SVG");
    sToolTipText = QT_TR_NOOP("Exports the current page as an SVG");
    sWhatsThis = "TechDraw_ExportPageSVG";
    sStatusTip = sToolTipText;
    sPixmap = "actions/TechDraw_ExportPageSVG";
}

void CmdTechDrawExportPageSVG::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    TechDraw::DrawPage* page = DrawGuiUtil::findPage(this);
    if (!page) {
        return;
    }

    Gui::Document* activeGui =
        Gui::Application::Instance->getDocument(page->getDocument());
    if (!activeGui) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("No drawing page"),
            QObject::tr("The drawing document is no longer open.")
        );
        return;
    }
    Gui::ViewProvider* vp = activeGui->getViewProvider(page);
    ViewProviderPage* vpPage = freecad_cast<ViewProviderPage*>(vp);

    if (vpPage) {
        vpPage->show();  // make sure a mdi will be available
        auto* mdiPage = vpPage->getMDIViewPage();
        if (!mdiPage) {
            QMessageBox::warning(
                Gui::getMainWindow(),
                QObject::tr("No drawing page"),
                QObject::tr(
                    "The drawing page could not be opened for export."
                )
            );
            return;
        }
        mdiPage->saveSVG();
    }
    else {
        QMessageBox::warning(Gui::getMainWindow(),
                             QObject::tr("No drawing page"),
                             QObject::tr("FreeCAD could not find a page to export"));
        return;
    }
}

bool CmdTechDrawExportPageSVG::isActive() { return DrawGuiUtil::needPage(this); }

//===========================================================================
// TechDraw_ExportPageDXF
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawExportPageDXF)

CmdTechDrawExportPageDXF::CmdTechDrawExportPageDXF() : Command("TechDraw_ExportPageDXF")
{
    sGroup = QT_TR_NOOP("File");
    sMenuText = QT_TR_NOOP("Export Page as DXF");
    sToolTipText = QT_TR_NOOP("Exports the current page as a DXF");
    sWhatsThis = "TechDraw_ExportPageDXF";
    sStatusTip = sToolTipText;
    sPixmap = "actions/TechDraw_ExportPageDXF";
}

void CmdTechDrawExportPageDXF::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    TechDraw::DrawPage* page = DrawGuiUtil::findPage(this);
    if (!page) {
        return;
    }
    TaskInternal::ObjectIdentity<TechDraw::DrawPage>
        pageIdentity(page);

    //WF? allow more than one TD Page per Dxf file??  1 TD page = 1 DXF file = 1 drawing?
    QString defaultDir;
    QString fileName = Gui::FileDialog::getSaveFileName(
        Gui::getMainWindow(), QObject::tr("Save DXF file"), defaultDir,
        Gui::FileDialog::FilterList{{QStringLiteral("DXF"), {"*.dxf"}}});

    if (fileName.isEmpty()) {
        return;
    }

    page = pageIdentity.resolve();
    if (!page) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Export page"),
            QObject::tr("The drawing page was closed before export.")
        );
        return;
    }
    doCommand(Doc, "import TechDraw");
    auto filespec = DU::cleanFilespecBackslash(
        Base::Tools::escapeEncodeFilename(fileName.toStdString()));
    const std::string pythonFilespec =
        Base::InterpreterSingleton::strToPython(filespec);
    const std::string pageCommand =
        Gui::Command::getObjectCmd(page);
    doCommand(
        Doc,
        "TechDraw.writeDXFPage(%s, '%s')",
        pageCommand.c_str(),
        pythonFilespec.c_str()
    );
}


bool CmdTechDrawExportPageDXF::isActive() { return DrawGuiUtil::needPage(this); }

//===========================================================================
// TechDraw_ProjectShape
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawProjectShape)

CmdTechDrawProjectShape::CmdTechDrawProjectShape() : Command("TechDraw_ProjectShape")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Project Shape");
    sToolTipText = QT_TR_NOOP("Creates a projected geometry of the selected object in the 3D view from the current camera angle");
    sWhatsThis = "TechDraw_ProjectShape";
    sStatusTip = sToolTipText;
    sPixmap = "actions/TechDraw_ProjectShape";
}

void CmdTechDrawProjectShape::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    Gui::TaskView::TaskDialog* dlg = Gui::Control().activeDialog();
    if (!dlg) {
        auto* document = getDocument();
        if (!document) {
            return;
        }
        std::vector<Part::Feature*> shapes =
            getSelection().getObjectsOfType<Part::Feature>();
        std::erase_if(
            shapes,
            [document](const Part::Feature* shape) {
                return !shape
                    || shape->getDocument() != document;
            }
        );
        if (shapes.empty()) {
            QMessageBox::warning(
                Gui::getMainWindow(),
                QObject::tr("No Shapes"),
                QObject::tr(
                    "Select at least one solid or surface in the active "
                    "document."
                )
            );
            return;
        }
        TaskInternal::showDocumentDialog(
            new TaskDlgProjection(document, shapes),
            document
        );
    }
}

bool CmdTechDrawProjectShape::isActive()
{
    return hasActiveDocument() && !Gui::Control().activeDialog();
}

void CreateTechDrawCommands()
{
    Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();

    rcCmdMgr.addCommand(new CmdTechDrawPageDefault());
    rcCmdMgr.addCommand(new CmdTechDrawPageTemplate());
    rcCmdMgr.addCommand(new CmdTechDrawRedrawPage());
    rcCmdMgr.addCommand(new CmdTechDrawPrintAll());
    rcCmdMgr.addCommand(new CmdTechDrawView());
    rcCmdMgr.addCommand(new CmdTechDrawActiveView());
    rcCmdMgr.addCommand(new CmdTechDrawSectionGroup());
    rcCmdMgr.addCommand(new CmdTechDrawSectionView());
    rcCmdMgr.addCommand(new CmdTechDrawComplexSection());
    rcCmdMgr.addCommand(new CmdTechDrawDetailView());
    rcCmdMgr.addCommand(new CmdTechDrawProjectionGroup());
    rcCmdMgr.addCommand(new CmdTechDrawClipGroup());
    rcCmdMgr.addCommand(new CmdTechDrawClipGroupAdd());
    rcCmdMgr.addCommand(new CmdTechDrawClipGroupRemove());
    rcCmdMgr.addCommand(new CmdTechDrawSymbol());
    rcCmdMgr.addCommand(new CmdTechDrawExportPageSVG());
    rcCmdMgr.addCommand(new CmdTechDrawExportPageDXF());
    rcCmdMgr.addCommand(new CmdTechDrawDraftView());
    rcCmdMgr.addCommand(new CmdTechDrawSpreadsheetView());
    rcCmdMgr.addCommand(new CmdTechDrawBalloon());
    rcCmdMgr.addCommand(new CmdTechDrawProjectShape());
    rcCmdMgr.addCommand(new CmdTechDrawBrokenView());

}

//****************************************


//! extract the selected shapes and xShapes and determine if a face has been
//! selected to define the projection direction
void getSelectedShapes(Gui::Command* cmd,
                      std::vector<App::DocumentObject*>& shapes,
                      std::vector<App::DocumentObject*>& xShapes,
                      App::DocumentObject*& faceObj,
                      std::string& faceName)
{
    Gui::ResolveMode resolve = Gui::ResolveMode::OldStyleElement;
    bool single = false;
    auto selection = cmd->getSelection().getSelectionEx(nullptr, App::DocumentObject::getClassTypeId(),
                                                   resolve, single);
    for (auto& sel : selection) {
        bool is_linked = false;
        auto obj = sel.getObject();
        if (!obj) {
            continue;
        }
        if (obj->isDerivedFrom<TechDraw::DrawPage>()) {
            continue;
        }
        if (obj->isDerivedFrom<App::LinkElement>()
            || obj->isDerivedFrom<App::LinkGroup>()
            || obj->isDerivedFrom<App::Link>()) {
            is_linked = true;
        }
        // If parent of the obj is a link to another document, we possibly need to treat non-link obj as linked, too
        // 1st, is obj in another document?
        if (obj->getDocument() != cmd->getDocument()) {
            std::set<App::DocumentObject*> parents = obj->getInListEx(true);
            for (auto& parent : parents) {
                // Only consider parents in the current document, i.e. possible links in this View's document
                if (parent->getDocument() != cmd->getDocument()) {
                    continue;
                }
                // 2nd, do we really have a link to obj?
                if (parent->isDerivedFrom<App::LinkElement>()
                    || parent->isDerivedFrom<App::LinkGroup>()
                    || parent->isDerivedFrom<App::Link>()) {
                    // We have a link chain from this document to obj, and obj is in another document -> it is an XLink target
                    is_linked = true;
                }
            }
        }
        if (is_linked) {
            xShapes.push_back(obj);
            continue;
        }
        //not a Link and not null.  assume to be drawable.  Undrawables will be
        // skipped later.
        shapes.push_back(obj);
        if (faceObj) {
            continue;
        }
        //don't know if this works for an XLink
        for (auto& sub : sel.getSubNames()) {
            if (TechDraw::DrawUtil::getGeomTypeFromName(sub) == "Face") {
                faceName = sub;
                //
                faceObj = obj;
                break;
            }
        }
    }
}

std::pair<Base::Vector3d, Base::Vector3d> viewDirection()
{
    if (!Preferences::useCameraDirection()) {
        return { Base::Vector3d(0, -1, 0), Base::Vector3d(1, 0, 0) };
    }

    auto faceInfo = faceFromSelection();
    if (faceInfo.first) {
        return DrawGuiUtil::getProjDirFromFace(faceInfo.first, faceInfo.second);
    }

    return DrawGuiUtil::get3DDirAndRot();
}

std::pair<App::DocumentObject*, std::string> faceFromSelection()
{
    auto selection = Gui::Selection().getSelectionEx(
        nullptr, App::DocumentObject::getClassTypeId(), Gui::ResolveMode::NoResolve);

    if (selection.empty()) {
        return { nullptr, "" };
    }

    for (auto& sel : selection) {
        for (auto& sub : sel.getSubNames()) {
            if (TechDraw::DrawUtil::getGeomTypeFromName(sub) == "Face") {
                return { sel.getObject(), sub };
            }
        }
    }

    return { nullptr, "" };
}

//! checks for directions that are almost +/- x,y,z.
Base::Vector3d checkDirectionVsBasis(Base::Vector3d dir)
{
    Base::Vector3d closest = DrawUtil::closestBasisOriented(dir);
    if (dir.IsEqual(closest, Precision::Confusion())) {
        return closest;
    }

    double angleDeg = Base::toDegrees(dir.GetAngle(closest));
    constexpr double MaxAngleDeg{1.0};  // absolutely a WAG.
    if (std::fabs(angleDeg) < MaxAngleDeg) {
        // close to a basis, but not quite equal
        auto msgText = QObject::tr("Selected Direction is within %1 degrees of a standard direction. "
                    "Replace selected Direction with %2?")
                    .arg(QString::number(angleDeg))
                    .arg(QString::fromStdString(DU::formatVector(closest)));
        QMessageBox::StandardButton rc = QMessageBox::question(
            Gui::getMainWindow(), QObject::tr("Direction is close to standard"),
            msgText,
            QMessageBox::StandardButtons(QMessageBox::Yes | QMessageBox::No));
        if (rc == QMessageBox::Yes) {
            return closest;
        }
    }

    // not close to a basis vector.
    return dir;

}
