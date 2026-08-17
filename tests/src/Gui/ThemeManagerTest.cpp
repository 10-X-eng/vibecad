// SPDX-License-Identifier: LGPL-2.1-or-later

#include <gtest/gtest.h>

#include <Gui/ThemeManager.h>

using Gui::ThemeManager;

TEST(ThemeManagerTest, RecognizesOnlyTheTwoProductModes)
{
    EXPECT_EQ(
        ThemeManager::modeFromStoredValues("Light", "", ""),
        ThemeManager::Mode::Light
    );
    EXPECT_EQ(
        ThemeManager::modeFromStoredValues("Dark", "", ""),
        ThemeManager::Mode::Dark
    );
    EXPECT_EQ(
        ThemeManager::modeFromStoredValues("system", "", ""),
        ThemeManager::Mode::Dark
    );
}

TEST(ThemeManagerTest, MigratesEveryPreviouslyBundledLightIdentifier)
{
    EXPECT_EQ(
        ThemeManager::modeFromStoredValues("", "VibeLight", ""),
        ThemeManager::Mode::Light
    );
    EXPECT_EQ(
        ThemeManager::modeFromStoredValues("", "FreeCAD Light", ""),
        ThemeManager::Mode::Light
    );
    EXPECT_EQ(
        ThemeManager::modeFromStoredValues("", "", "VibeLight.qss"),
        ThemeManager::Mode::Light
    );
    EXPECT_EQ(
        ThemeManager::modeFromStoredValues("", "", "FreeCAD Light.qss"),
        ThemeManager::Mode::Light
    );
    EXPECT_EQ(
        ThemeManager::modeFromStoredValues("", "", "OpenLight.qss"),
        ThemeManager::Mode::Light
    );
}

TEST(ThemeManagerTest, DefaultsLegacyAndUnknownValuesToDark)
{
    for (const char* legacy : {"", "Classic", "VibeDark", "FreeCAD Dark", "Dark behave"}) {
        EXPECT_EQ(
            ThemeManager::modeFromStoredValues("", legacy, ""),
            ThemeManager::Mode::Dark
        );
    }
    EXPECT_EQ(
        ThemeManager::modeFromStoredValues("", "", "OpenDark.qss"),
        ThemeManager::Mode::Dark
    );
    EXPECT_EQ(
        ThemeManager::modeFromStoredValues("", "third-party-theme", "custom.qss"),
        ThemeManager::Mode::Dark
    );
}

TEST(ThemeManagerTest, ExposesStableModeAndResourceNames)
{
    EXPECT_STREQ(ThemeManager::modeName(ThemeManager::Mode::Light), "Light");
    EXPECT_STREQ(ThemeManager::modeName(ThemeManager::Mode::Dark), "Dark");
    EXPECT_STREQ(ThemeManager::styleSheetName(ThemeManager::Mode::Light), "VibeLight.qss");
    EXPECT_STREQ(ThemeManager::styleSheetName(ThemeManager::Mode::Dark), "VibeDark.qss");
    EXPECT_STREQ(
        ThemeManager::overlayStyleSheetName(ThemeManager::Mode::Light),
        "VibeLight_Overlay.qss"
    );
    EXPECT_STREQ(
        ThemeManager::overlayStyleSheetName(ThemeManager::Mode::Dark),
        "VibeDark_Overlay.qss"
    );
}

TEST(ThemeManagerTest, StartupPreservesOverridesAfterAProductModeWasStored)
{
    const auto light = ThemeManager::startupPlanFromStoredValues("Light", "", "");
    EXPECT_EQ(light.mode, ThemeManager::Mode::Light);
    EXPECT_FALSE(light.loadProfile);

    const auto dark = ThemeManager::startupPlanFromStoredValues("Dark", "", "");
    EXPECT_EQ(dark.mode, ThemeManager::Mode::Dark);
    EXPECT_FALSE(dark.loadProfile);
}

TEST(ThemeManagerTest, StartupLoadsAProfileForFirstRunAndLegacyMigration)
{
    const auto firstRun = ThemeManager::startupPlanFromStoredValues("", "", "");
    EXPECT_EQ(firstRun.mode, ThemeManager::Mode::Dark);
    EXPECT_TRUE(firstRun.loadProfile);

    const auto legacyLight =
        ThemeManager::startupPlanFromStoredValues("", "VibeLight", "");
    EXPECT_EQ(legacyLight.mode, ThemeManager::Mode::Light);
    EXPECT_TRUE(legacyLight.loadProfile);
}
