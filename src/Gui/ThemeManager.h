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
 *                                                                         *
 *   VibeCAD is distributed in the hope that it will be useful, but        *
 *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU      *
 *   Lesser General Public License for more details.                       *
 ***************************************************************************/

#pragma once

#include <string>
#include <string_view>

#include <QObject>

#include <FCGlobal.h>

namespace Gui
{

/**
 * Owns VibeCAD's complete, deliberately small appearance contract.
 *
 * Light and Dark are profiles of appearance values only. Unlike preference
 * packs, applying a theme cannot replace unrelated user preferences, restore
 * toolbar state, or execute macros.
 */
class GuiExport ThemeManager: public QObject
{
    Q_OBJECT

public:
    enum class Mode
    {
        Light,
        Dark
    };

    struct StartupPlan
    {
        Mode mode;
        bool loadProfile;
    };

    ThemeManager() = default;
    ~ThemeManager() override = default;

    [[nodiscard]] Mode currentMode() const;
    [[nodiscard]] static Mode modeFromStoredValues(
        std::string_view appearanceMode,
        std::string_view legacyTheme,
        std::string_view legacyStyleSheet
    );
    [[nodiscard]] static StartupPlan startupPlanFromStoredValues(
        std::string_view appearanceMode,
        std::string_view legacyTheme,
        std::string_view legacyStyleSheet
    );
    [[nodiscard]] static const char* modeName(Mode mode);
    [[nodiscard]] static const char* styleSheetName(Mode mode);
    [[nodiscard]] static const char* overlayStyleSheetName(Mode mode);

    /**
     * Apply the selected appearance profile.
     *
     * Set refreshGui to false during startup, before the normal style setup
     * has run. Runtime callers should use the default.
     */
    bool apply(Mode mode, bool refreshGui = true);
    bool applyCurrent(bool refreshGui = true);

Q_SIGNALS:
    void modeChanged(Mode mode);

private:
    bool applyImpl(Mode mode, bool refreshGui, bool loadProfile);
};

}  // namespace Gui
