/***************************************************************************
 *   Copyright (c) 2009 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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

#include "DlgSettingsUI.h"
#include "ui_DlgSettingsUI.h"

using namespace Gui::Dialog;

/* TRANSLATOR Gui::Dialog::DlgSettingsUI */

/**
 *  Constructs a DlgSettingsUI which is a child of 'parent', with the
 *  name 'name' and widget flags set to 'f'
 */
DlgSettingsUI::DlgSettingsUI(QWidget* parent)
    : PreferencePage(parent)
    , ui(new Ui_DlgSettingsUI)
{
    ui->setupUi(this);
}

/**
 *  Destroys the object and frees any allocated resources
 */
DlgSettingsUI::~DlgSettingsUI() = default;

void DlgSettingsUI::saveSettings()
{
    // Tree View
    ui->fontSizeSpinBox->onSave();
    ui->iconSizeSpinBox->onSave();
    ui->resizableColumnsCheckBox->onSave();
    ui->showVisibilityIconCheckBox->onSave();
    ui->hideDescriptionCheckBox->onSave();
    ui->hideInternalNamesCheckBox->onSave();
    ui->hideTreeViewScrollBarCheckBox->onSave();
    ui->hideHeaderCheckBox->onSave();

    // Overlay
    ui->hideTabBarCheckBox->onSave();
    ui->hintShowTabBarCheckBox->onSave();
    ui->hidePropertyViewScrollBarCheckBox->onSave();
    ui->overlayAutoHideCheckBox->onSave();
    ui->mouseClickPassThroughCheckBox->onSave();
    ui->mouseWheelPassThroughCheckBox->onSave();

    // TaskWatcher
    ui->showTaskWatcherCheckBox->onSave();
}

void DlgSettingsUI::loadSettings()
{
    // Tree View
    ui->fontSizeSpinBox->onRestore();
    ui->iconSizeSpinBox->onRestore();
    ui->resizableColumnsCheckBox->onRestore();
    ui->showVisibilityIconCheckBox->onRestore();
    ui->hideDescriptionCheckBox->onRestore();
    ui->hideInternalNamesCheckBox->onRestore();
    ui->hideTreeViewScrollBarCheckBox->onRestore();
    ui->hideHeaderCheckBox->onRestore();

    // Overlay
    ui->hideTabBarCheckBox->onRestore();
    ui->hintShowTabBarCheckBox->onRestore();
    ui->hidePropertyViewScrollBarCheckBox->onRestore();
    ui->overlayAutoHideCheckBox->onRestore();
    ui->mouseClickPassThroughCheckBox->onRestore();
    ui->mouseWheelPassThroughCheckBox->onRestore();

    // TaskWatcher
    ui->showTaskWatcherCheckBox->onRestore();

}

/**
 * Sets the strings of the subwidgets using the current language.
 */
void DlgSettingsUI::changeEvent(QEvent* e)
{
    if (e->type() == QEvent::LanguageChange) {
        ui->retranslateUi(this);
    }
    else {
        QWidget::changeEvent(e);
    }
}

#include "moc_DlgSettingsUI.cpp"
