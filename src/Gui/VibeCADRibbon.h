// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2026 VibeCAD contributors                               *
 *                                                                         *
 *   This file is part of VibeCAD.                                         *
 *                                                                         *
 *   VibeCAD is free software: you can redistribute it and/or modify it     *
 *   under the terms of the GNU Lesser General Public License as           *
 *   published by the Free Software Foundation, either version 2.1 of the  *
 *   License, or (at your option) any later version.                       *
 ***************************************************************************/

#pragma once

#include <memory>

#include <QObject>

#include <FCGlobal.h>

namespace Gui
{

class MainWindow;

/**
 * Installs VibeCAD's task-oriented application chrome.
 *
 * Workbenches remain the internal command/module lifecycle, while users see
 * stable CAD domains. Existing QAction instances are reused so shortcuts,
 * enablement, check state, and command groups retain their native behavior.
 */
class GuiExport VibeCADRibbon: public QObject
{
public:
    static VibeCADRibbon* install(MainWindow* mainWindow);
    ~VibeCADRibbon() override;

protected:
    bool eventFilter(QObject* watched, QEvent* event) override;

private:
    explicit VibeCADRibbon(MainWindow* mainWindow);

    struct Private;
    std::unique_ptr<Private> d;
};

}  // namespace Gui
