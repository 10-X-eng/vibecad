// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2002 Jürgen Riegel <juergen.riegel@web.de>              *
 *   Copyright (c) 2015 Eivind Kvedalen <eivind@kvedalen.name>             *
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


#include <algorithm>
#include <ranges>
#include <set>
#include <sstream>
#include <vector>

#include <FCConfig.h>

#include <QFileInfo>
#include <QMessageBox>

#if defined(FC_OS_WIN32)
# include <sys/timeb.h>
#endif

#include <App/Application.h>
#include <App/Document.h>
#include <Base/Exception.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/CommandT.h>
#include <Gui/Document.h>
#include <Gui/ExactTransaction.h>
#include <Gui/FileDialog.h>
#include <Gui/MainWindow.h>
#include <Gui/Selection/Selection.h>
#include <Mod/Spreadsheet/App/Cell.h>
#include <Mod/Spreadsheet/App/Sheet.h>

#include "MutationSupport.h"
#include "PropertiesDialog.h"
#include "SpreadsheetView.h"
#include "ViewProviderSpreadsheet.h"


//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

using namespace SpreadsheetGui;
using namespace Spreadsheet;
using namespace Base;
using namespace App;

//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

namespace
{

struct ActiveSheet
{
    SpreadsheetGui::SheetView* view = nullptr;
    Spreadsheet::Sheet* sheet = nullptr;
    App::Document* document = nullptr;

    explicit operator bool() const noexcept
    {
        return view && sheet && document;
    }
};

ActiveSheet activeSheet(const bool requireCleanBoundary)
{
    auto* view = freecad_cast<SpreadsheetGui::SheetView*>(Gui::getMainWindow()->activeWindow());
    auto* sheet = view ? view->getSheet() : nullptr;
    auto* document = sheet ? sheet->getDocument() : nullptr;
    if (!view || !sheet || !document || App::GetApplication().getActiveDocument() != document
        || (requireCleanBoundary && !SpreadsheetGui::MutationSupport::hasCleanBoundary(document))) {
        return {};
    }
    return {view, sheet, document};
}

App::Document* cleanActiveDocument()
{
    auto* document = App::GetApplication().getActiveDocument();
    return SpreadsheetGui::MutationSupport::hasCleanBoundary(document) ? document : nullptr;
}

void showSpreadsheetError(const QString& title, const Base::Exception& error)
{
    QMessageBox::warning(Gui::getMainWindow(), title, QString::fromUtf8(error.what()));
}

template<typename Mutation>
void mutateSelectedRanges(const char* transactionName, Mutation&& mutation)
{
    ActiveSheet context = activeSheet(true);
    if (!context) {
        return;
    }
    const std::vector<Range> ranges = context.view->selectedRanges();
    if (ranges.empty()) {
        return;
    }

    Gui::ExactTransaction transaction(*context.document, transactionName);
    mutation(*context.sheet, ranges);
    SpreadsheetGui::MutationSupport::recompute(*context.document);
    SpreadsheetGui::MutationSupport::commit(transaction);
    Gui::Command::updateActive();
}

bool hasSelectedRanges()
{
    const ActiveSheet context = activeSheet(true);
    return context && !context.view->selectedRanges().empty();
}

void applyAlignment(const char* transactionName, const char* alignment)
{
    try {
        mutateSelectedRanges(
            transactionName,
            [alignment](Sheet& sheet, const std::vector<Range>& ranges) {
                for (const auto& range : ranges) {
                    Gui::cmdAppObjectArgs(
                        &sheet,
                        "setAlignment('%s', '%s', 'keep')",
                        range.rangeString().c_str(),
                        alignment
                    );
                }
            }
        );
    }
    catch (const Base::Exception& error) {
        showSpreadsheetError(QObject::tr("Align Spreadsheet Cells"), error);
    }
}

void toggleStyle(const char* transactionName, const char* styleName)
{
    try {
        ActiveSheet context = activeSheet(true);
        if (!context) {
            return;
        }
        const QModelIndexList selection = context.view->selectedIndexes();
        const std::vector<Range> ranges = context.view->selectedRanges();
        if (selection.empty() || ranges.empty()) {
            return;
        }

        const bool allStyled = std::ranges::all_of(
            selection,
            [sheet = context.sheet, styleName](const QModelIndex& index) {
                const Cell* cell = sheet->getCell(CellAddress(index.row(), index.column()));
                std::set<std::string> styles;
                return cell && cell->getStyle(styles) && styles.contains(styleName);
            }
        );

        Gui::ExactTransaction transaction(*context.document, transactionName);
        for (const auto& range : ranges) {
            Gui::cmdAppObjectArgs(
                context.sheet,
                "setStyle('%s', '%s', '%s')",
                range.rangeString().c_str(),
                styleName,
                allStyled ? "remove" : "add"
            );
        }
        SpreadsheetGui::MutationSupport::recompute(*context.document);
        SpreadsheetGui::MutationSupport::commit(transaction);
        Gui::Command::updateActive();
    }
    catch (const Base::Exception& error) {
        showSpreadsheetError(QObject::tr("Format Spreadsheet Cells"), error);
    }
}

}  // namespace

//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

DEF_STD_CMD_A(CmdSpreadsheetMergeCells)

CmdSpreadsheetMergeCells::CmdSpreadsheetMergeCells()
    : Command("Spreadsheet_MergeCells")
{
    sAppModule = "Spreadsheet";
    sGroup = QT_TR_NOOP("Spreadsheet");
    sMenuText = QT_TR_NOOP("&Merge Cells");
    sToolTipText = QT_TR_NOOP("Merges the selected cells");
    sWhatsThis = "Spreadsheet_MergeCells";
    sStatusTip = sToolTipText;
    sPixmap = "SpreadsheetMergeCells";
}

void CmdSpreadsheetMergeCells::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    try {
        ActiveSheet context = activeSheet(true);
        if (!context) {
            return;
        }
        std::vector<Range> ranges;
        for (const auto& range : context.view->selectedRanges()) {
            if (range.size() > 1) {
                ranges.push_back(range);
            }
        }
        if (ranges.empty()) {
            return;
        }

        Gui::ExactTransaction transaction(
            *context.document,
            QT_TRANSLATE_NOOP("Command", "Merge cells")
        );
        bool changed = false;
        for (const auto& range : ranges) {
            changed = context.sheet->mergeCells(range) || changed;
        }
        if (!changed) {
            return;
        }
        SpreadsheetGui::MutationSupport::recompute(*context.document);
        SpreadsheetGui::MutationSupport::commit(transaction);
        Gui::Command::updateActive();
    }
    catch (const Base::Exception& error) {
        showSpreadsheetError(QObject::tr("Merge Cells"), error);
    }
}

bool CmdSpreadsheetMergeCells::isActive()
{
    const ActiveSheet context = activeSheet(true);
    return context && context.view->selectedIndexesRaw().size() > 1;
}

//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

DEF_STD_CMD_A(CmdSpreadsheetSplitCell)

CmdSpreadsheetSplitCell::CmdSpreadsheetSplitCell()
    : Command("Spreadsheet_SplitCell")
{
    sAppModule = "Spreadsheet";
    sGroup = QT_TR_NOOP("Spreadsheet");
    sMenuText = QT_TR_NOOP("Sp&lit Cell");
    sToolTipText = QT_TR_NOOP("Splits a previously merged cell");
    sWhatsThis = "Spreadsheet_SplitCell";
    sStatusTip = sToolTipText;
    sPixmap = "SpreadsheetSplitCell";
}

void CmdSpreadsheetSplitCell::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    try {
        ActiveSheet context = activeSheet(true);
        if (!context) {
            return;
        }
        const QModelIndex current = context.view->currentIndex();
        const CellAddress address(current.row(), current.column());
        if (!current.isValid() || !context.sheet->isMergedCell(address)) {
            return;
        }

        Gui::ExactTransaction transaction(*context.document, QT_TRANSLATE_NOOP("Command", "Split cell"));
        context.sheet->splitCell(address);
        SpreadsheetGui::MutationSupport::recompute(*context.document);
        SpreadsheetGui::MutationSupport::commit(transaction);
        Gui::Command::updateActive();
    }
    catch (const Base::Exception& error) {
        showSpreadsheetError(QObject::tr("Split Cell"), error);
    }
}

bool CmdSpreadsheetSplitCell::isActive()
{
    const ActiveSheet context = activeSheet(true);
    if (!context) {
        return false;
    }
    const QModelIndex current = context.view->currentIndex();
    return current.isValid() && context.view->selectedIndexesRaw().size() == 1
        && context.sheet->isMergedCell(CellAddress(current.row(), current.column()));
}

//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

DEF_STD_CMD_A(CmdSpreadsheetImport)

CmdSpreadsheetImport::CmdSpreadsheetImport()
    : Command("Spreadsheet_Import")
{
    sAppModule = "Spreadsheet";
    sGroup = QT_TR_NOOP("Spreadsheet");
    sMenuText = QT_TR_NOOP("&Import Spreadsheet");
    sToolTipText = QT_TR_NOOP("Imports a CSV file into a new spreadsheet");
    sWhatsThis = "Spreadsheet_Import";
    sStatusTip = sToolTipText;
    sPixmap = "SpreadsheetImport";
}

void CmdSpreadsheetImport::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    auto* document = cleanActiveDocument();
    if (!document) {
        return;
    }
    const Gui::FileDialog::FilterList formatList {
        {QStringLiteral("CSV"), {"*.csv"}},
        Gui::FileDialog::Filter::AllFiles(),
    };
    QString fileName = Gui::FileDialog::getOpenFileName(
        Gui::getMainWindow(),
        QObject::tr("Import file"),
        QString(),
        formatList
    );
    if (fileName.isEmpty() || document != App::GetApplication().getActiveDocument()
        || !SpreadsheetGui::MutationSupport::hasCleanBoundary(document)) {
        return;
    }

    try {
        Gui::ExactTransaction transaction(*document, QT_TRANSLATE_NOOP("Command", "Import spreadsheet"));
        const std::string objectName = document->getUniqueObjectName("Spreadsheet");
        auto* sheet = document->addObject<Spreadsheet::Sheet>(objectName.c_str());
        if (!sheet) {
            throw Base::RuntimeError("The imported spreadsheet could not be created");
        }

        char delimiter;
        char quote;
        char escape;
        std::string errorMessage = "Import";
        if (!sheet->getCharsFromPrefs(delimiter, quote, escape, errorMessage)) {
            throw Base::ValueError(errorMessage);
        }
        if (!sheet->importFromFile(fileName.toStdString(), delimiter, quote, escape)) {
            throw Base::RuntimeError("The selected CSV file could not be imported");
        }
        sheet->Label.setValue(QFileInfo(fileName).completeBaseName().toUtf8().constData());
        SpreadsheetGui::MutationSupport::publishCreatedSheet(*sheet);
        SpreadsheetGui::MutationSupport::recompute(*document);
        SpreadsheetGui::MutationSupport::commit(transaction);
        Gui::Selection().clearSelection();
        Gui::Selection().addSelection(document->getName(), sheet->getNameInDocument());
        Gui::Command::updateActive();
    }
    catch (const Base::Exception& error) {
        showSpreadsheetError(QObject::tr("Import Spreadsheet"), error);
    }
}

bool CmdSpreadsheetImport::isActive()
{
    return cleanActiveDocument() != nullptr;
}

//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

DEF_STD_CMD_A(CmdSpreadsheetExport)

CmdSpreadsheetExport::CmdSpreadsheetExport()
    : Command("Spreadsheet_Export")
{
    sAppModule = "Spreadsheet";
    sGroup = QT_TR_NOOP("Spreadsheet");
    sMenuText = QT_TR_NOOP("&Export Spreadsheet");
    sToolTipText = QT_TR_NOOP("Exports the spreadsheet to a CSV file");
    sWhatsThis = "Spreadsheet_Export";
    sStatusTip = sToolTipText;
    sPixmap = "SpreadsheetExport";
}

void CmdSpreadsheetExport::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    const ActiveSheet context = activeSheet(true);
    if (!context) {
        return;
    }
    Gui::ViewProvider* provider = Gui::Application::Instance->getViewProvider(context.sheet);
    auto* sheetProvider = freecad_cast<ViewProviderSheet*>(provider);
    if (sheetProvider) {
        sheetProvider->exportAsFile();
    }
}

bool CmdSpreadsheetExport::isActive()
{
    return static_cast<bool>(activeSheet(true));
}

//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

DEF_STD_CMD_A(CmdSpreadsheetAlignLeft)

CmdSpreadsheetAlignLeft::CmdSpreadsheetAlignLeft()
    : Command("Spreadsheet_AlignLeft")
{
    sAppModule = "Spreadsheet";
    sGroup = QT_TR_NOOP("Spreadsheet");
    sMenuText = QT_TR_NOOP("Align &Left");
    sToolTipText = QT_TR_NOOP("Aligns cell contents to the left");
    sWhatsThis = "Spreadsheet_AlignLeft";
    sStatusTip = sToolTipText;
    sPixmap = "SpreadsheetAlignLeft";
}

void CmdSpreadsheetAlignLeft::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    applyAlignment(QT_TRANSLATE_NOOP("Command", "Left-align cells"), "left");
}

bool CmdSpreadsheetAlignLeft::isActive()
{
    return hasSelectedRanges();
}

//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

DEF_STD_CMD_A(CmdSpreadsheetAlignCenter)

CmdSpreadsheetAlignCenter::CmdSpreadsheetAlignCenter()
    : Command("Spreadsheet_AlignCenter")
{
    sAppModule = "Spreadsheet";
    sGroup = QT_TR_NOOP("Spreadsheet");
    sMenuText = QT_TR_NOOP("Align Horizontal &Center");
    sToolTipText = QT_TR_NOOP("Aligns cell contents to the horizontal center");
    sWhatsThis = "Spreadsheet_AlignCenter";
    sStatusTip = sToolTipText;
    sPixmap = "SpreadsheetAlignCenter";
}

void CmdSpreadsheetAlignCenter::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    applyAlignment(QT_TRANSLATE_NOOP("Command", "Center cells"), "center");
}

bool CmdSpreadsheetAlignCenter::isActive()
{
    return hasSelectedRanges();
}

//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

DEF_STD_CMD_A(CmdSpreadsheetAlignRight)

CmdSpreadsheetAlignRight::CmdSpreadsheetAlignRight()
    : Command("Spreadsheet_AlignRight")
{
    sAppModule = "Spreadsheet";
    sGroup = QT_TR_NOOP("Spreadsheet");
    sMenuText = QT_TR_NOOP("Align &Right");
    sToolTipText = QT_TR_NOOP("Aligns cell contents to the right");
    sWhatsThis = "Spreadsheet_AlignRight";
    sStatusTip = sToolTipText;
    sPixmap = "SpreadsheetAlignRight";
}

void CmdSpreadsheetAlignRight::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    applyAlignment(QT_TRANSLATE_NOOP("Command", "Right-align cells"), "right");
}

bool CmdSpreadsheetAlignRight::isActive()
{
    return hasSelectedRanges();
}

//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

DEF_STD_CMD_A(CmdSpreadsheetAlignTop)

CmdSpreadsheetAlignTop::CmdSpreadsheetAlignTop()
    : Command("Spreadsheet_AlignTop")
{
    sAppModule = "Spreadsheet";
    sGroup = QT_TR_NOOP("Spreadsheet");
    sMenuText = QT_TR_NOOP("Align &Top");
    sToolTipText = QT_TR_NOOP("Aligns cell contents to the top");
    sWhatsThis = "Spreadsheet_AlignTop";
    sStatusTip = sToolTipText;
    sPixmap = "SpreadsheetAlignTop";
}

void CmdSpreadsheetAlignTop::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    applyAlignment(QT_TRANSLATE_NOOP("Command", "Top-align cells"), "top");
}

bool CmdSpreadsheetAlignTop::isActive()
{
    return hasSelectedRanges();
}

//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

DEF_STD_CMD_A(CmdSpreadsheetAlignBottom)

CmdSpreadsheetAlignBottom::CmdSpreadsheetAlignBottom()
    : Command("Spreadsheet_AlignBottom")
{
    sAppModule = "Spreadsheet";
    sGroup = QT_TR_NOOP("Spreadsheet");
    sMenuText = QT_TR_NOOP("Align &Bottom");
    sToolTipText = QT_TR_NOOP("Aligns cell contents to the bottom");
    sWhatsThis = "Spreadsheet_AlignBottom";
    sStatusTip = sToolTipText;
    sPixmap = "SpreadsheetAlignBottom";
}

void CmdSpreadsheetAlignBottom::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    applyAlignment(QT_TRANSLATE_NOOP("Command", "Bottom-align cells"), "bottom");
}

bool CmdSpreadsheetAlignBottom::isActive()
{
    return hasSelectedRanges();
}

//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

DEF_STD_CMD_A(CmdSpreadsheetAlignVCenter)

CmdSpreadsheetAlignVCenter::CmdSpreadsheetAlignVCenter()
    : Command("Spreadsheet_AlignVCenter")
{
    sAppModule = "Spreadsheet";
    sGroup = QT_TR_NOOP("Spreadsheet");
    sMenuText = QT_TR_NOOP("Align &Vertical Center");
    sToolTipText = QT_TR_NOOP("Aligns cell contents to the vertical center");
    sWhatsThis = "Spreadsheet_AlignVCenter";
    sStatusTip = sToolTipText;
    sPixmap = "SpreadsheetAlignVCenter";
}

void CmdSpreadsheetAlignVCenter::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    applyAlignment(QT_TRANSLATE_NOOP("Command", "Vertically center cells"), "vcenter");
}

bool CmdSpreadsheetAlignVCenter::isActive()
{
    return hasSelectedRanges();
}

//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

DEF_STD_CMD_A(CmdSpreadsheetStyleBold)

CmdSpreadsheetStyleBold::CmdSpreadsheetStyleBold()
    : Command("Spreadsheet_StyleBold")
{
    sAppModule = "Spreadsheet";
    sGroup = QT_TR_NOOP("Spreadsheet");
    sMenuText = QT_TR_NOOP("&Bold Text");
    sToolTipText = QT_TR_NOOP("Sets the text in the selected cells bold");
    sWhatsThis = "Spreadsheet_StyleBold";
    sStatusTip = sToolTipText;
    sPixmap = "SpreadsheetStyleBold";
    sAccel = "Ctrl+B";
}

void CmdSpreadsheetStyleBold::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    toggleStyle(QT_TRANSLATE_NOOP("Command", "Toggle bold text"), "bold");
}

bool CmdSpreadsheetStyleBold::isActive()
{
    return hasSelectedRanges();
}

//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

DEF_STD_CMD_A(CmdSpreadsheetStyleItalic)

CmdSpreadsheetStyleItalic::CmdSpreadsheetStyleItalic()
    : Command("Spreadsheet_StyleItalic")
{
    sAppModule = "Spreadsheet";
    sGroup = QT_TR_NOOP("Spreadsheet");
    sMenuText = QT_TR_NOOP("&Italic Text");
    sToolTipText = QT_TR_NOOP("Sets the text in the selected cells italic");
    sWhatsThis = "Spreadsheet_StyleItalic";
    sStatusTip = sToolTipText;
    sPixmap = "SpreadsheetStyleItalic";
    sAccel = "Ctrl+I";
}

void CmdSpreadsheetStyleItalic::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    toggleStyle(QT_TRANSLATE_NOOP("Command", "Toggle italic text"), "italic");
}

bool CmdSpreadsheetStyleItalic::isActive()
{
    return hasSelectedRanges();
}

//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

DEF_STD_CMD_A(CmdSpreadsheetStyleUnderline)

CmdSpreadsheetStyleUnderline::CmdSpreadsheetStyleUnderline()
    : Command("Spreadsheet_StyleUnderline")
{
    sAppModule = "Spreadsheet";
    sGroup = QT_TR_NOOP("Spreadsheet");
    sMenuText = QT_TR_NOOP("&Underline Text");
    sToolTipText = QT_TR_NOOP("Underlines the text in the selected cells");
    sWhatsThis = "Spreadsheet_StyleUnderline";
    sStatusTip = sToolTipText;
    sPixmap = "SpreadsheetStyleUnderline";
    sAccel = "Ctrl+U";
}

void CmdSpreadsheetStyleUnderline::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    toggleStyle(QT_TRANSLATE_NOOP("Command", "Toggle underline text"), "underline");
}

bool CmdSpreadsheetStyleUnderline::isActive()
{
    return hasSelectedRanges();
}

//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

DEF_STD_CMD_A(CmdSpreadsheetSetAlias)

CmdSpreadsheetSetAlias::CmdSpreadsheetSetAlias()
    : Command("Spreadsheet_SetAlias")
{
    sAppModule = "Spreadsheet";
    sGroup = QT_TR_NOOP("Spreadsheet");
    sMenuText = QT_TR_NOOP("Set Alias");
    sToolTipText = QT_TR_NOOP("Sets an alias for the selected cell");
    sWhatsThis = "Spreadsheet_SetAlias";
    sStatusTip = sToolTipText;
    sAccel = "Ctrl+Shift+A";
    sPixmap = "SpreadsheetAlias";
}

void CmdSpreadsheetSetAlias::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    ActiveSheet context = activeSheet(true);
    if (!context) {
        return;
    }
    const QModelIndexList selection = context.view->selectedIndexes();
    if (selection.size() != 1) {
        return;
    }

    std::vector<Range> range;
    range.emplace_back(
        selection[0].row(),
        selection[0].column(),
        selection[0].row(),
        selection[0].column()
    );

    PropertiesDialog dialog(context.sheet, range, context.view);
    dialog.selectAlias();
    if (dialog.exec() == QDialog::Accepted && context.document
        && context.sheet->getDocument() == context.document) {
        dialog.apply();
    }
}

bool CmdSpreadsheetSetAlias::isActive()
{
    const ActiveSheet context = activeSheet(true);
    return context && context.view->selectedIndexes().size() == 1;
}

//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

DEF_STD_CMD_A(CmdSpreadsheetCellProperties)

CmdSpreadsheetCellProperties::CmdSpreadsheetCellProperties()
    : Command("Spreadsheet_CellProperties")
{
    sAppModule = "Spreadsheet";
    sGroup = QT_TR_NOOP("Spreadsheet");
    sMenuText = QT_TR_NOOP("Cell Properties…");
    sToolTipText = QT_TR_NOOP("Edits formatting, colors, units, and aliases");
    sWhatsThis = "Spreadsheet_CellProperties";
    sStatusTip = sToolTipText;
    sPixmap = "preferences-spreadsheet";
}

void CmdSpreadsheetCellProperties::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    ActiveSheet context = activeSheet(true);
    if (!context) {
        return;
    }
    const std::vector<Range> ranges = context.view->selectedRanges();
    if (ranges.empty()) {
        return;
    }

    PropertiesDialog dialog(context.sheet, ranges, context.view);
    if (dialog.exec() == QDialog::Accepted && context.document
        && context.sheet->getDocument() == context.document) {
        dialog.apply();
    }
}

bool CmdSpreadsheetCellProperties::isActive()
{
    return hasSelectedRanges();
}

//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

DEF_STD_CMD_A(CmdCreateSpreadsheet)

CmdCreateSpreadsheet::CmdCreateSpreadsheet()
    : Command("Spreadsheet_CreateSheet")
{
    sAppModule = "Spreadsheet";
    sGroup = QT_TR_NOOP("Spreadsheet");
    sMenuText = QT_TR_NOOP("&New Spreadsheet");
    sToolTipText = QT_TR_NOOP("Creates a new spreadsheet");
    sWhatsThis = "Spreadsheet_CreateSheet";
    sStatusTip = sToolTipText;
    sPixmap = "Spreadsheet";
}

void CmdCreateSpreadsheet::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    auto* document = cleanActiveDocument();
    if (!document) {
        return;
    }
    try {
        Gui::ExactTransaction transaction(*document, QT_TRANSLATE_NOOP("Command", "Create spreadsheet"));
        const std::string objectName = document->getUniqueObjectName("Spreadsheet");
        auto* sheet = document->addObject<Spreadsheet::Sheet>(objectName.c_str());
        if (!sheet) {
            throw Base::RuntimeError("The spreadsheet could not be created");
        }
        SpreadsheetGui::MutationSupport::publishCreatedSheet(*sheet);
        SpreadsheetGui::MutationSupport::recompute(*document);
        SpreadsheetGui::MutationSupport::commit(transaction);
        Gui::Selection().clearSelection();
        Gui::Selection().addSelection(document->getName(), sheet->getNameInDocument());
        Gui::Command::updateActive();
    }
    catch (const Base::Exception& error) {
        showSpreadsheetError(QObject::tr("Create Spreadsheet"), error);
    }
}

bool CmdCreateSpreadsheet::isActive()
{
    return cleanActiveDocument() != nullptr;
}

//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

void CreateSpreadsheetCommands()
{
    Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();

    rcCmdMgr.addCommand(new CmdCreateSpreadsheet());

    rcCmdMgr.addCommand(new CmdSpreadsheetMergeCells());
    rcCmdMgr.addCommand(new CmdSpreadsheetSplitCell());

    rcCmdMgr.addCommand(new CmdSpreadsheetImport());
    rcCmdMgr.addCommand(new CmdSpreadsheetExport());

    rcCmdMgr.addCommand(new CmdSpreadsheetAlignLeft());
    rcCmdMgr.addCommand(new CmdSpreadsheetAlignCenter());
    rcCmdMgr.addCommand(new CmdSpreadsheetAlignRight());

    rcCmdMgr.addCommand(new CmdSpreadsheetAlignTop());
    rcCmdMgr.addCommand(new CmdSpreadsheetAlignVCenter());
    rcCmdMgr.addCommand(new CmdSpreadsheetAlignBottom());

    rcCmdMgr.addCommand(new CmdSpreadsheetStyleBold());
    rcCmdMgr.addCommand(new CmdSpreadsheetStyleItalic());
    rcCmdMgr.addCommand(new CmdSpreadsheetStyleUnderline());

    rcCmdMgr.addCommand(new CmdSpreadsheetSetAlias());
    rcCmdMgr.addCommand(new CmdSpreadsheetCellProperties());
}
