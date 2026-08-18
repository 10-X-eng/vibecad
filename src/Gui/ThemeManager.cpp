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

#include "ThemeManager.h"

#include <filesystem>

#include <QString>

#include <App/Application.h>
#include <Base/Console.h>
#include <Base/Parameter.h>

#include "Application.h"
#include "OverlayManager.h"

namespace fs = std::filesystem;

namespace
{

bool matches(std::string_view value, std::initializer_list<const char*> accepted)
{
    const QString candidate =
        QString::fromUtf8(value.data(), static_cast<int>(value.size())).trimmed();
    for (const char* item : accepted) {
        if (candidate.compare(QString::fromLatin1(item), Qt::CaseInsensitive) == 0) {
            return true;
        }
    }
    return false;
}

ParameterGrp::handle mainWindowParameters()
{
    return App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/MainWindow"
    );
}

fs::path profilePath(Gui::ThemeManager::Mode mode)
{
    return fs::path(Base::FileInfo::stringToPath(App::Application::getResourceDir())) / "Gui"
        / "Themes" / (std::string(Gui::ThemeManager::modeName(mode)) + ".cfg");
}

}  // namespace

Gui::ThemeManager::Mode Gui::ThemeManager::currentMode() const
{
    const auto parameters = mainWindowParameters();
    return modeFromStoredValues(
        parameters->GetASCII("AppearanceMode"),
        parameters->GetASCII("Theme"),
        parameters->GetASCII("StyleSheet")
    );
}

Gui::ThemeManager::Mode Gui::ThemeManager::modeFromStoredValues(
    std::string_view appearanceMode,
    std::string_view legacyTheme,
    std::string_view legacyStyleSheet
)
{
    if (matches(appearanceMode, {"Light"})) {
        return Mode::Light;
    }
    if (matches(appearanceMode, {"Dark"})) {
        return Mode::Dark;
    }

    // Exact, documented migrations from every previously bundled light theme.
    if (matches(legacyTheme, {"Light", "VibeLight", "FreeCAD Light"})
        || matches(legacyStyleSheet, {"VibeLight.qss", "FreeCAD Light.qss", "OpenLight.qss"})) {
        return Mode::Light;
    }

    // Dark is the product default. This also deliberately absorbs Classic,
    // system, removed third-party selections, and malformed legacy values.
    return Mode::Dark;
}

Gui::ThemeManager::StartupPlan Gui::ThemeManager::startupPlanFromStoredValues(
    std::string_view appearanceMode,
    std::string_view legacyTheme,
    std::string_view legacyStyleSheet
)
{
    if (matches(appearanceMode, {"Light"})) {
        return {Mode::Light, false};
    }
    if (matches(appearanceMode, {"Dark"})) {
        return {Mode::Dark, false};
    }
    return {
        modeFromStoredValues(appearanceMode, legacyTheme, legacyStyleSheet),
        true,
    };
}

const char* Gui::ThemeManager::modeName(Mode mode)
{
    return mode == Mode::Light ? "Light" : "Dark";
}

const char* Gui::ThemeManager::styleSheetName(Mode mode)
{
    return mode == Mode::Light ? "VibeLight.qss" : "VibeDark.qss";
}

const char* Gui::ThemeManager::overlayStyleSheetName(Mode mode)
{
    return mode == Mode::Light ? "VibeLight_Overlay.qss" : "VibeDark_Overlay.qss";
}

bool Gui::ThemeManager::apply(Mode mode, bool refreshGui)
{
    return applyImpl(mode, refreshGui, true);
}

bool Gui::ThemeManager::applyImpl(Mode mode, bool refreshGui, bool loadProfile)
{
    bool applied = !loadProfile;
    const fs::path path = profilePath(mode);

    if (loadProfile) {
        try {
            if (fs::is_regular_file(path)) {
                auto profile = ParameterManager::Create();
                profile->LoadDocument(Base::FileInfo::pathToString(path).c_str());
                profile->GetGroup("BaseApp")
                    ->insertTo(App::GetApplication().GetUserParameter().GetGroup("BaseApp"));
                applied = true;
            }
            else {
                Base::Console().error(
                    "VibeCAD %s theme profile is missing: %s\n",
                    modeName(mode),
                    Base::FileInfo::pathToString(path).c_str()
                );
            }
        }
        catch (const std::exception& error) {
            Base::Console().error(
                "VibeCAD could not load the %s theme profile: %s\n",
                modeName(mode),
                error.what()
            );
        }
    }

    // These values are the stable public appearance contract. Set them even
    // if a damaged installation is missing the color profile so startup still
    // produces a usable interface.
    const auto parameters = mainWindowParameters();
    parameters->SetASCII("AppearanceMode", modeName(mode));
    parameters->SetASCII("Theme", modeName(mode));
    parameters->SetASCII("QtStyle", "Fusion");
    parameters->SetASCII("StyleSheet", styleSheetName(mode));
    parameters->SetASCII("OverlayActiveStyleSheet", overlayStyleSheetName(mode));
    parameters->SetASCII(
        "ThemeStyleParametersFile",
        std::string("qss:parameters/") + modeName(mode) + ".yaml"
    );

    const auto retiredCustomization = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/Themes"
    );
    retiredCustomization->RemoveUnsigned("ThemeAccentColor1");
    retiredCustomization->RemoveUnsigned("ThemeAccentColor2");
    retiredCustomization->RemoveUnsigned("ThemeAccentColor3");

    if (refreshGui && Application::Instance) {
        // Fusion is installed during startup. Replacing the application-owned QStyle here can
        // invalidate explicit widget style pointers while the widget tree is still alive.
        Application::Instance->styleParameterManager()->reload();
        Application::Instance->setStyleSheet(
            QString::fromLatin1(styleSheetName(mode)),
            parameters->GetBool("TiledBackground", false)
        );
        OverlayManager::instance()->refresh(nullptr, true);
    }

    Q_EMIT modeChanged(mode);
    return applied;
}

bool Gui::ThemeManager::applyCurrent(bool refreshGui)
{
    const auto parameters = mainWindowParameters();
    const auto plan = startupPlanFromStoredValues(
        parameters->GetASCII("AppearanceMode"),
        parameters->GetASCII("Theme"),
        parameters->GetASCII("StyleSheet")
    );
    return applyImpl(plan.mode, refreshGui, plan.loadProfile);
}
