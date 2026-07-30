// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2005 Werner Mayer <wmayer[at]users.sourceforge.net>     *
 *   Copyright (c) 2015 Eivind Kvedalen (eivind@kvedalen.name)             *
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


#include <QMessageBox>
#include <QToolBar>
#include <qobject.h>


#include "Mod/Spreadsheet/App/Sheet.h"
#include "Mod/Spreadsheet/Gui/SpreadsheetView.h"
#include <App/Document.h>
#include <App/Range.h>
#include <Base/Exception.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/CommandT.h>
#include <Gui/ExactTransaction.h>
#include <Gui/MainWindow.h>
#include <Gui/MenuManager.h>
#include <Gui/ToolBarManager.h>

#include "MutationSupport.h"
#include "Workbench.h"
#include "qtcolorpicker.h"


using namespace Base;
using namespace App;
using namespace SpreadsheetGui;
using namespace Spreadsheet;

#if 0  // needed for Qt's lupdate utility
    qApp->translate("Workbench", "Spreadsheet");
    qApp->translate("Workbench", "&Spreadsheet");
    qApp->translate("Workbench", "&Alignment");
    qApp->translate("Workbench", "&Styles");
#endif

/// @namespace ImageGui @class Workbench
TYPESYSTEM_SOURCE(SpreadsheetGui::Workbench, Gui::StdWorkbench)

Workbench::Workbench()
    : Gui::StdWorkbench()
    , initialized(false)
    , workbenchHelper(new WorkbenchHelper)
{}

Workbench::~Workbench() = default;

void Workbench::activated()
{
    if (!initialized) {
        QList<QToolBar*> bars = Gui::getMainWindow()->findChildren<QToolBar*>(
            QStringLiteral("Spreadsheet")
        );

        if (bars.size() == 1) {
            QToolBar* bar = bars[0];
            QtColorPicker* foregroundColor;
            QtColorPicker* backgroundColor;
            QPalette palette = Gui::getMainWindow()->palette();

            QList<QtColorPicker*> fgList = Gui::getMainWindow()->findChildren<QtColorPicker*>(
                QStringLiteral("Spreadsheet_ForegroundColor")
            );
            if (!fgList.empty()) {
                foregroundColor = fgList[0];
            }
            else {
                foregroundColor = new QtColorPicker(bar, palette.color(QPalette::WindowText));
                foregroundColor->setObjectName(QStringLiteral("Spreadsheet_ForegroundColor"));
                foregroundColor->setStandardColors();
                QObject::connect(
                    foregroundColor,
                    &QtColorPicker::colorSet,
                    workbenchHelper.get(),
                    &WorkbenchHelper::setForegroundColor
                );
                QObject::connect(
                    foregroundColor,
                    &QtColorPicker::colorCleared,
                    workbenchHelper.get(),
                    &WorkbenchHelper::clearForegroundColor
                );
            }
            foregroundColor->setToolTip(QObject::tr("Sets the text color of cells"));
            foregroundColor->setWhatsThis(QObject::tr("Sets the text color of spreadsheet cells"));
            foregroundColor->setStatusTip(QObject::tr("Sets the text color of spreadsheet cells"));
            bar->addWidget(foregroundColor);

            QList<QtColorPicker*> bgList = Gui::getMainWindow()->findChildren<QtColorPicker*>(
                QStringLiteral("Spreadsheet_BackgroundColor")
            );
            if (!bgList.empty()) {
                backgroundColor = bgList[0];
            }
            else {
                backgroundColor = new QtColorPicker(bar, palette.color(QPalette::Base));
                backgroundColor->setObjectName(QStringLiteral("Spreadsheet_BackgroundColor"));
                backgroundColor->setStandardColors();
                QObject::connect(
                    backgroundColor,
                    &QtColorPicker::colorSet,
                    workbenchHelper.get(),
                    &WorkbenchHelper::setBackgroundColor
                );
                QObject::connect(
                    backgroundColor,
                    &QtColorPicker::colorCleared,
                    workbenchHelper.get(),
                    &WorkbenchHelper::clearBackgroundColor
                );
            }
            backgroundColor->setToolTip(QObject::tr("Sets the background color of cells"));
            backgroundColor->setWhatsThis(QObject::tr("Sets the spreadsheet cells background color"));
            backgroundColor->setStatusTip(QObject::tr("Sets the background color of cells"));
            bar->addWidget(backgroundColor);

            initialized = true;
        }
    }
}

namespace
{

template<typename Action>
void mutateSelectedColors(const char* transactionName, const QString& errorTitle, Action&& action)
{
    auto* sheetView = freecad_cast<SpreadsheetGui::SheetView*>(Gui::getMainWindow()->activeWindow());
    auto* sheet = sheetView ? sheetView->getSheet() : nullptr;
    auto* document = sheet ? sheet->getDocument() : nullptr;
    const std::vector<Range> ranges = sheetView ? sheetView->selectedRanges() : std::vector<Range>();
    if (!sheet || !document || ranges.empty()) {
        return;
    }
    try {
        MutationSupport::requireCleanBoundary(*document);
        Gui::ExactTransaction transaction(*document, transactionName);
        for (const auto& range : ranges) {
            action(*sheet, range);
        }
        MutationSupport::recompute(*document);
        MutationSupport::commit(transaction);
        Gui::Command::updateActive();
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(Gui::getMainWindow(), errorTitle, QString::fromUtf8(error.what()));
    }
}

}  // namespace

void WorkbenchHelper::setForegroundColor(const QColor& color)
{
    mutateSelectedColors(
        QT_TRANSLATE_NOOP("Command", "Set text color"),
        tr("Text Color"),
        [&color](Sheet& sheet, const Range& range) {
            Gui::cmdAppObjectArgs(
                &sheet,
                "setForeground('%s', (%f,%f,%f))",
                range.rangeString().c_str(),
                color.redF(),
                color.greenF(),
                color.blueF()
            );
        }
    );
}

void SpreadsheetGui::WorkbenchHelper::clearForegroundColor()
{
    mutateSelectedColors(
        QT_TRANSLATE_NOOP("Command", "Clear text color"),
        tr("Text Color"),
        [](Sheet& sheet, const Range& range) {
            Gui::cmdAppObjectArgs(&sheet, "clearForeground('%s')", range.rangeString().c_str());
        }
    );
}

void WorkbenchHelper::setBackgroundColor(const QColor& color)
{
    mutateSelectedColors(
        QT_TRANSLATE_NOOP("Command", "Set background color"),
        tr("Background Color"),
        [&color](Sheet& sheet, const Range& range) {
            Gui::cmdAppObjectArgs(
                &sheet,
                "setBackground('%s', (%f,%f,%f))",
                range.rangeString().c_str(),
                color.redF(),
                color.greenF(),
                color.blueF()
            );
        }
    );
}

void SpreadsheetGui::WorkbenchHelper::clearBackgroundColor()
{
    mutateSelectedColors(
        QT_TRANSLATE_NOOP("Command", "Clear background color"),
        tr("Background Color"),
        [](Sheet& sheet, const Range& range) {
            Gui::cmdAppObjectArgs(&sheet, "clearBackground('%s')", range.rangeString().c_str());
        }
    );
}

Gui::MenuItem* Workbench::setupMenuBar() const
{
    Gui::MenuItem* root = StdWorkbench::setupMenuBar();
    Gui::MenuItem* item = root->findItem("&Windows");

    Gui::MenuItem* spreadsheet = new Gui::MenuItem;
    root->insertItem(item, spreadsheet);

    // utilities
    Gui::MenuItem* alignments = new Gui::MenuItem;
    alignments->setCommand("&Alignment");
    *alignments << "Spreadsheet_AlignLeft"
                << "Spreadsheet_AlignCenter"
                << "Spreadsheet_AlignRight"
                << "Spreadsheet_AlignTop"
                << "Spreadsheet_AlignVCenter"
                << "Spreadsheet_AlignBottom";

    Gui::MenuItem* styles = new Gui::MenuItem;
    styles->setCommand("&Styles");
    *styles << "Spreadsheet_StyleBold"
            << "Spreadsheet_StyleItalic"
            << "Spreadsheet_StyleUnderline";

    spreadsheet->setCommand("&Spreadsheet");
    *spreadsheet << "Spreadsheet_CreateSheet"
                 << "Separator"
                 << "Spreadsheet_Import"
                 << "Spreadsheet_Export"
                 << "Separator"
                 << "Spreadsheet_MergeCells"
                 << "Spreadsheet_SplitCell"
                 << "Spreadsheet_CellProperties"
                 << "Separator" << alignments << styles;

    return root;
}

Gui::ToolBarItem* Workbench::setupToolBars() const
{
    Gui::ToolBarItem* root = StdWorkbench::setupToolBars();
    Gui::ToolBarItem* part = new Gui::ToolBarItem(root);
    part->setCommand("Spreadsheet");
    *part << "Spreadsheet_CreateSheet"
          << "Separator"
          << "Spreadsheet_Import"
          << "Spreadsheet_Export"
          << "Separator"
          << "Spreadsheet_MergeCells"
          << "Spreadsheet_SplitCell"
          << "Spreadsheet_CellProperties"
          << "Separator"
          << "Spreadsheet_AlignLeft"
          << "Spreadsheet_AlignCenter"
          << "Spreadsheet_AlignRight"
          << "Spreadsheet_AlignTop"
          << "Spreadsheet_AlignVCenter"
          << "Spreadsheet_AlignBottom"
          << "Separator"
          << "Spreadsheet_StyleBold"
          << "Spreadsheet_StyleItalic"
          << "Spreadsheet_StyleUnderline"
          << "Separator"
          << "Spreadsheet_SetAlias"
          << "Separator";

    return root;
}

Gui::ToolBarItem* Workbench::setupCommandBars() const
{
    Gui::ToolBarItem* root = new Gui::ToolBarItem;
    Gui::ToolBarItem* ss = new Gui::ToolBarItem(root);
    ss->setCommand("Spreadsheet");
    *ss << "Spreadsheet_Open";
    return root;
}

#include "moc_Workbench.cpp"
