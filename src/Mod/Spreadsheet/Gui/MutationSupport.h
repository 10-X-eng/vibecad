// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <Mod/Spreadsheet/SpreadsheetGlobal.h>

namespace App
{
class Document;
}

namespace Gui
{
class ExactTransaction;
}

namespace Spreadsheet
{
class Sheet;
}

namespace SpreadsheetGui::MutationSupport
{

SpreadsheetGuiExport bool hasCleanBoundary(const App::Document* document) noexcept;

SpreadsheetGuiExport void requireCleanBoundary(const App::Document& document);

SpreadsheetGuiExport void recompute(App::Document& document);

SpreadsheetGuiExport void commit(Gui::ExactTransaction& transaction);

SpreadsheetGuiExport void publishCreatedSheet(Spreadsheet::Sheet& sheet);

}  // namespace SpreadsheetGui::MutationSupport
