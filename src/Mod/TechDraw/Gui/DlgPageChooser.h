/****************************************************************************
 *   Copyright (c) 2021 Wanderer Fan <wandererfan@gmail.com>                *
 *                                                                          *
 *   This file is part of the FreeCAD CAx development system.               *
 *                                                                          *
 *   This library is free software; you can redistribute it and/or          *
 *   modify it under the terms of the GNU Library General Public            *
 *   License as published by the Free Software Foundation; either           *
 *   version 2 of the License, or (at your option) any later version.       *
 *                                                                          *
 *   This library  is distributed in the hope that it will be useful,       *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of         *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the          *
 *   GNU Library General Public License for more details.                   *
 *                                                                          *
 *   You should have received a copy of the GNU Library General Public      *
 *   License along with this library; see the file COPYING.LIB. If not,     *
 *   write to the Free Software Foundation, Inc., 59 Temple Place,          *
 *   Suite 330, Boston, MA  02111-1307, USA                                 *
 *                                                                          *
 ****************************************************************************/
#pragma once

#include <string>
#include <vector>

#include <Mod/TechDraw/TechDrawGlobal.h>

#include <QDialog>

namespace TechDrawGui {

class Ui_DlgPageChooser;

//NOLINTBEGIN
class TechDrawGuiExport DlgPageChooser : public QDialog
{
    Q_OBJECT
//NOLINTEND

public:
    DlgPageChooser(const std::vector<std::string>& labels,
                   const std::vector<std::string>& names,
                   QWidget* parent = nullptr, Qt::WindowFlags fl = Qt::WindowFlags());
    ~DlgPageChooser() override;

    std::string getSelection() const;
    /**
     * Return the original input position for the selected page.
     *
     * Page object names are unique only within one document.  Callers which
     * offer pages from several documents must use this identity-preserving
     * index instead of resolving getSelection() in whichever document happens
     * to be active after the chooser closes.
     */
    int getSelectionIndex() const;
    void accept() override;
    void reject() override;

public Q_SLOTS:
    void slotChangedSelection();

private:
    void fillList(std::vector<std::string> labels, std::vector<std::string> names);

    Ui_DlgPageChooser* ui;
};

} // namespace Gui
