// SPDX-License-Identifier: LGPL-2.1-or-later

/****************************************************************************
 *   Copyright (c) 2004 Werner Mayer <wmayer[at]users.sourceforge.net>     *
 *   Copyright (c) 2023 FreeCAD Project Association                         *
 *                                                                          *
 *   This file is part of FreeCAD.                                          *
 *                                                                          *
 *   FreeCAD is free software: you can redistribute it and/or modify it     *
 *   under the terms of the GNU Lesser General Public License as            *
 *   published by the Free Software Foundation, either version 2.1 of the   *
 *   License, or (at your option) any later version.                        *
 *                                                                          *
 *   FreeCAD is distributed in the hope that it will be useful, but         *
 *   WITHOUT ANY WARRANTY; without even the implied warranty of             *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU       *
 *   Lesser General Public License for more details.                        *
 *                                                                          *
 *   You should have received a copy of the GNU Lesser General Public       *
 *   License along with FreeCAD. If not, see                                *
 *   <https://www.gnu.org/licenses/>.                                       *
 *                                                                          *
 ***************************************************************************/


#include <cmath>
#include <limits>
#include <QApplication>
#include <QLocale>
#include <QString>
#include <algorithm>

#include <App/Document.h>
#include <Base/Parameter.h>
#include <Base/UnitsApi.h>

#include <Gui/Document.h>
#include <Gui/Command.h>

#include <Gui/Action.h>
#include <Gui/Application.h>
#include <Gui/MainWindow.h>
#include <Gui/OverlayManager.h>
#include <Gui/ParamHandler.h>
#include <Gui/ThemeManager.h>
#include <Gui/Language/Translator.h>

#include "DlgSettingsGeneral.h"
#include "ui_DlgSettingsGeneral.h"

using namespace Gui;
using namespace Gui::Dialog;
using Base::QuantityFormat;
using Base::UnitsApi;

/* TRANSLATOR Gui::Dialog::DlgSettingsGeneral */

/**
 *  Constructs a DlgSettingsGeneral which is a child of 'parent', with the
 *  name 'name' and widget flags set to 'f'
 *
 *  The dialog will by default be modeless, unless you set 'modal' to
 *  true to construct a modal dialog.
 */
DlgSettingsGeneral::DlgSettingsGeneral(QWidget* parent)
    : PreferencePage(parent)
    , localeIndex(0)
    , themeChanged(false)
    , ui(new Ui_DlgSettingsGeneral)
{
    ui->setupUi(this);

    for (const char* option : Translator::formattingOptions) {
        ui->UseLocaleFormatting->addItem(QCoreApplication::translate("Gui::Translator", option));
    }

    connect(
        ui->themesCombobox,
        qOverload<int>(&QComboBox::activated),
        this,
        &DlgSettingsGeneral::onThemeChanged
    );

    connect(
        ui->comboBox_UnitSystem,
        qOverload<int>(&QComboBox::currentIndexChanged),
        this,
        &DlgSettingsGeneral::onUnitSystemIndexChanged
    );
    ui->spinBoxDecimals->setMaximum(std::numeric_limits<double>::digits10 + 1);

    auto addItem = [&, index {0}](const std::string& item) mutable {
        ui->comboBox_UnitSystem->addItem(QString::fromStdString(item), index++);
    };
    auto descriptions = UnitsApi::getDescriptions();
    std::for_each(descriptions.begin(), descriptions.end(), addItem);

    // Enable/disable the fractional inch option depending on system
    const auto visible = UnitsApi::isMultiUnitLength();
    ui->comboBox_FracInch->setVisible(visible);
    ui->fractionalInchLabel->setVisible(visible);
}

/**
 *  Destroys the object and frees any allocated resources
 */
DlgSettingsGeneral::~DlgSettingsGeneral() = default;

/** Sets the size of the recent file list from the user parameters.
 * @see RecentFilesAction
 * @see StdCmdRecentFiles
 */
void DlgSettingsGeneral::setRecentFileSize()
{
    auto recent = getMainWindow()->findChild<RecentFilesAction*>(QLatin1String("recentFiles"));
    if (recent) {
        ParameterGrp::handle hGrp = WindowParameter::getDefaultParameter()->GetGroup("RecentFiles");
        recent->resizeList(hGrp->GetInt("RecentFiles", 4));
    }
}

bool DlgSettingsGeneral::setLanguage()
{
    ParameterGrp::handle hGrp = WindowParameter::getDefaultParameter()->GetGroup("General");
    QString lang = QLocale::languageToString(QLocale().language());
    QByteArray language = hGrp->GetASCII("Language", (const char*)lang.toLatin1()).c_str();
    QByteArray current = ui->Languages->itemData(ui->Languages->currentIndex()).toByteArray();
    if (current != language) {
        hGrp->SetASCII("Language", current.constData());
        Translator::instance()->activateLanguage(current.constData());
        return true;
    }
    return false;
}

void DlgSettingsGeneral::setNumberLocale(bool force /* = false*/)
{
    int localeFormat = ui->UseLocaleFormatting->currentIndex();

    // Only make the change if locale setting has changed or if forced
    // Except if format is "OS" where we don't want to run setLocale
    if (localeIndex == localeFormat && (!force || localeFormat == 0)) {
        return;
    }
    localeIndex = localeFormat;
}

void DlgSettingsGeneral::saveUnitSystemSettings()
{
    ParameterGrp::handle hGrpu = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/Units"
    );
    hGrpu->SetInt("UserSchema", ui->comboBox_UnitSystem->currentIndex());
    hGrpu->SetInt("Decimals", ui->spinBoxDecimals->value());
    hGrpu->SetBool("IgnoreProjectSchema", ui->checkBox_projectUnitSystemIgnore->isChecked());

    // Set actual value
    UnitsApi::setDecimals(ui->spinBoxDecimals->value());

    // Convert the combobox index to the its integer denominator. Currently
    // with 1/2, 1/4, through 1/128, this little equation directly computes the
    // denominator given the combobox integer.
    //
    // The inverse conversion is done when loaded. That way only one thing (the
    // numerical fractional inch value) needs to be stored.

    // minimum fractional inch to display
    int FracInch = std::pow(2, ui->comboBox_FracInch->currentIndex() + 1);
    hGrpu->SetInt("FracInch", FracInch);

    // Set the actual format value
    UnitsApi::setDenominator(FracInch);

    const int selectedSchema = ui->comboBox_UnitSystem->currentIndex();

    // Set and save the Unit System
    if (ui->checkBox_projectUnitSystemIgnore->isChecked()) {
        // Use the preference for the current view without changing the
        // document's own unit system.
        UnitsApi::setSchema(selectedSchema);
    }
    else if (App::GetApplication().getActiveDocument()) {
        // A preference change with a project open must also update that
        // project's unit system. Otherwise the old project schema is restored
        // immediately and the model appears to break when units are changed.
        getMainWindow()->setUserSchema(selectedSchema);
    }
    else {
        // if there is no existing document then the unit must still be set
        UnitsApi::setSchema(selectedSchema);
    }

    ui->SubstituteDecimal->onSave();
    ui->UseLocaleFormatting->onSave();
}

void DlgSettingsGeneral::saveSettings()
{
    saveUnitSystemSettings();

    ui->RecentFiles->onSave();
    ui->EnableCursorBlinking->onSave();
    ui->SplashScreen->onSave();
    ui->ActivateOverlay->onSave();
    if (property("ActivateOverlay").toBool() != ui->ActivateOverlay->isChecked()) {
        requireRestart();
    }
    ui->FineGrainedRecompute->onSave();

    setRecentFileSize();
    bool force = setLanguage();
    // In case type is "Selected language", we need to force locale change
    setNumberLocale(force);

    ParameterGrp::handle hGrp = WindowParameter::getDefaultParameter()->GetGroup("General");
    QVariant size = ui->toolbarIconSize->itemData(ui->toolbarIconSize->currentIndex());
    int pixel = size.toInt();
    hGrp->SetInt("ToolbarIconSize", pixel);
    getMainWindow()->setIconSize(QSize(pixel, pixel));

    int blinkTime {hGrp->GetBool("EnableCursorBlinking", true) ? -1 : 0};
    qApp->setCursorFlashTime(blinkTime);

    saveDockWindowVisibility();

    hGrp = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/MainWindow"
    );
    hGrp->SetBool("TiledBackground", ui->tiledBackground->isChecked());

    if (themeChanged) {
        saveThemes();
    }
}

void DlgSettingsGeneral::loadSettings()
{
    int FracInch;
    int cbIndex;

    ParameterGrp::handle hGrpu = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/Units"
    );
    ui->comboBox_UnitSystem->setCurrentIndex(hGrpu->GetInt("UserSchema", 0));
    ui->spinBoxDecimals->setValue(hGrpu->GetInt("Decimals", UnitsApi::getDecimals()));
    ui->checkBox_projectUnitSystemIgnore->setChecked(hGrpu->GetBool("IgnoreProjectSchema", false));

    // Get the current user setting for the minimum fractional inch
    FracInch = hGrpu->GetInt("FracInch", UnitsApi::getDenominator());

    // Convert fractional inch to the corresponding combobox index using this
    // handy little equation.
    cbIndex = std::log2(FracInch) - 1;
    ui->comboBox_FracInch->setCurrentIndex(cbIndex);
    ui->SubstituteDecimal->onRestore();
    ui->UseLocaleFormatting->onRestore();
    ui->RecentFiles->onRestore();
    ui->EnableCursorBlinking->onRestore();
    ui->SplashScreen->onRestore();
    ui->ActivateOverlay->onRestore();
    setProperty("ActivateOverlay", ui->ActivateOverlay->isChecked());
    ui->FineGrainedRecompute->onRestore();

    // search for the language files
    ParameterGrp::handle hGrp = WindowParameter::getDefaultParameter()->GetGroup("General");
    auto langToStr = Translator::instance()->activeLanguage();
    QByteArray language = hGrp->GetASCII("Language", langToStr.c_str()).c_str();

    localeIndex = ui->UseLocaleFormatting->currentIndex();

    int index = 1;
    TStringMap list = Translator::instance()->supportedLocales();
    ui->Languages->clear();
    ui->Languages->addItem(QStringLiteral("English"), QByteArray("English"));
    for (auto it = list.begin(); it != list.end(); ++it, index++) {
        QByteArray lang = it->first.c_str();
        QString langname = QString::fromLatin1(lang.constData());

        if (it->second == "sr-CS") {
            // Qt does not treat sr-CS (Serbian, Latin) as a Latin-script variant by default: this
            // forces it to do so.
            it->second = "sr_Latn";
        }

        QLocale locale(QString::fromLatin1(it->second.c_str()));
        QString native = locale.nativeLanguageName();
        if (!native.isEmpty()) {
            if (native[0].isLetter()) {
                native[0] = native[0].toUpper();
            }
            langname = native;
        }

        ui->Languages->addItem(langname, lang);
        if (language == lang) {
            ui->Languages->setCurrentIndex(index);
        }
    }

    QAbstractItemModel* model = ui->Languages->model();
    if (model) {
        model->sort(0);
    }

    addIconSizes(getCurrentIconSize());

    // TreeMode combobox setup.
    loadDockWindowVisibility();

    hGrp = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/MainWindow"
    );
    ui->tiledBackground->setChecked(hGrp->GetBool("TiledBackground", false));

    loadThemes();
}

void DlgSettingsGeneral::resetSettingsToDefaults()
{
    ParameterGrp::handle hGrp;
    hGrp = App::GetApplication().GetParameterGroupByPath("User parameter:BaseApp/Preferences/Units");
    // reset "UserSchema" parameter
    hGrp->RemoveInt("UserSchema");
    // reset "Decimals" parameter
    hGrp->RemoveInt("Decimals");
    // reset "IgnoreProjectSchema" parameter
    hGrp->RemoveBool("IgnoreProjectSchema");
    // reset "FracInch" parameter
    hGrp->RemoveInt("FracInch");

    hGrp = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/MainWindow"
    );
    // reset "Theme" parameter
    hGrp->RemoveASCII("AppearanceMode");
    hGrp->RemoveASCII("Theme");
    hGrp->RemoveASCII("StyleSheet");
    hGrp->RemoveASCII("OverlayActiveStyleSheet");
    hGrp->RemoveASCII("ThemeStyleParametersFile");
    hGrp->RemoveASCII("QtStyle");
    // reset "TiledBackground" parameter
    hGrp->RemoveBool("TiledBackground");


    hGrp = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/DockWindows"
    );
    // reset "ComboView" parameters
    hGrp->GetGroup("ComboView")->RemoveBool("Enabled");
    // reset "TreeView" parameters
    hGrp->GetGroup("TreeView")->RemoveBool("Enabled");
    // reset "PropertyView" parameters
    hGrp->GetGroup("PropertyView")->RemoveBool("Enabled");

    hGrp = WindowParameter::getDefaultParameter()->GetGroup("General");
    // reset "Language" parameter
    hGrp->RemoveASCII("Language");
    // reset "ToolbarIconSize" parameter
    hGrp->RemoveInt("ToolbarIconSize");

    // finally reset all the parameters associated to Gui::Pref* widgets
    PreferencePage::resetSettingsToDefaults();
}

void DlgSettingsGeneral::saveThemes()
{
    const auto mode = static_cast<ThemeManager::Mode>(
        ui->themesCombobox->currentData().toInt()
    );
    Application::Instance->themeManager()->apply(mode);
    themeChanged = false;
}

void DlgSettingsGeneral::loadThemes()
{
    ui->themesCombobox->clear();
    ui->themesCombobox->addItem(tr("Light"), static_cast<int>(ThemeManager::Mode::Light));
    ui->themesCombobox->addItem(tr("Dark"), static_cast<int>(ThemeManager::Mode::Dark));

    const int current = static_cast<int>(Application::Instance->themeManager()->currentMode());
    ui->themesCombobox->setCurrentIndex(ui->themesCombobox->findData(current));
}

int DlgSettingsGeneral::getCurrentIconSize() const
{
    ParameterGrp::handle hGrp = WindowParameter::getDefaultParameter()->GetGroup("General");
    int current = getMainWindow()->iconSize().width();
    return hGrp->GetInt("ToolbarIconSize", current);
}

void DlgSettingsGeneral::addIconSizes(int current)
{
    ui->toolbarIconSize->clear();

    QList<int> sizes {16, 24, 32, 48};
    if (!sizes.contains(current)) {
        sizes.append(current);
    }

    for (int size : sizes) {
        ui->toolbarIconSize->addItem(QString(), QVariant(size));
    }

    int index = ui->toolbarIconSize->findData(QVariant(current));
    ui->toolbarIconSize->setCurrentIndex(index);
    translateIconSizes();
}

void DlgSettingsGeneral::translateIconSizes()
{
    auto getSize = [this](int index) {
        return ui->toolbarIconSize->itemData(index).toInt();
    };

    QStringList sizes;
    sizes << tr("Small (%1px)").arg(getSize(0));
    sizes << tr("Medium (%1px)").arg(getSize(1));
    sizes << tr("Large (%1px)").arg(getSize(2));
    sizes << tr("Extra large (%1px)").arg(getSize(3));
    if (ui->toolbarIconSize->count() > 4) {
        sizes << tr("Custom (%1px)").arg(getSize(4));
    }

    for (int index = 0; index < sizes.size(); index++) {
        ui->toolbarIconSize->setItemText(index, sizes[index]);
    }
}

void DlgSettingsGeneral::retranslateUnits()
{
    auto setItem = [&, index {0}](const std::string& item) mutable {
        ui->comboBox_UnitSystem->setItemText(index++, QString::fromStdString(item));
    };
    const auto descriptions = UnitsApi::getDescriptions();
    std::for_each(descriptions.begin(), descriptions.end(), setItem);
}

void DlgSettingsGeneral::changeEvent(QEvent* event)
{
    if (event->type() == QEvent::LanguageChange) {
        translateIconSizes();
        retranslateUnits();
        int index = ui->UseLocaleFormatting->currentIndex();
        ui->retranslateUi(this);
        ui->UseLocaleFormatting->setCurrentIndex(index);
    }
    else {
        QWidget::changeEvent(event);
    }
}

void DlgSettingsGeneral::saveDockWindowVisibility()
{
    auto hGrp = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/DockWindows"
    );
    bool treeView = hGrp->GetGroup("TreeView")->GetBool("Enabled", true);
    bool propertyView = hGrp->GetGroup("PropertyView")->GetBool("Enabled", false);
    bool comboView = hGrp->GetGroup("ComboView")->GetBool("Enabled", false);

    int index = -1;
    if (treeView && propertyView) {
        index = 2;
    }
    else if (treeView || propertyView) {
        index = 1;
    }
    else if (comboView) {
        index = 0;
    }

    if (index != ui->treeMode->currentIndex()) {
        requireRestart();
    }

    switch (ui->treeMode->currentIndex()) {
        case 0:
            comboView = true;
            treeView = propertyView = false;
            break;
        case 1:
            comboView = propertyView = false;
            treeView = true;
            break;
        case 2:
            comboView = false;
            treeView = propertyView = true;
            break;
    }

    hGrp->GetGroup("ComboView")->SetBool("Enabled", comboView);
    hGrp->GetGroup("TreeView")->SetBool("Enabled", treeView);
    hGrp->GetGroup("PropertyView")->SetBool("Enabled", propertyView);
}

void DlgSettingsGeneral::loadDockWindowVisibility()
{
    ui->treeMode->clear();
    ui->treeMode->addItem(tr("Combined"));
    ui->treeMode->addItem(tr("Tree only"));
    ui->treeMode->addItem(tr("Tree and property"));

    auto hGrp = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/DockWindows"
    );
    bool propertyView = hGrp->GetGroup("PropertyView")->GetBool("Enabled", false);
    bool treeView = hGrp->GetGroup("TreeView")->GetBool("Enabled", true);
    bool comboView = hGrp->GetGroup("ComboView")->GetBool("Enabled", false);
    int index = -1;
    if (treeView && propertyView) {
        index = 2;
    }
    else if (treeView || propertyView) {
        index = 1;
    }
    else if (comboView) {
        index = 0;
    }
    ui->treeMode->setCurrentIndex(index);
}

void DlgSettingsGeneral::onUnitSystemIndexChanged(const int index)
{
    if (index < 0) {
        return;  // happens when clearing the combo box in retranslateUi()
    }

    // Enable/disable the fractional inch option depending on system
    const auto schema = UnitsApi::createSchema(index);
    const auto visible = schema->isMultiUnitLength();
    ui->comboBox_FracInch->setVisible(visible);
    ui->fractionalInchLabel->setVisible(visible);
}

void DlgSettingsGeneral::onThemeChanged(int index)
{
    Q_UNUSED(index);
    themeChanged = true;
}

///////////////////////////////////////////////////////////
namespace
{

class ApplyDockWidget: public ParamHandler
{
public:
    bool onChange(const ParamKey*) override
    {
        OverlayManager::instance()->reload(OverlayManager::ReloadMode::ReloadPause);
        return true;
    }

    void onTimer() override
    {
        getMainWindow()->initDockWindows(true);
        OverlayManager::instance()->reload(OverlayManager::ReloadMode::ReloadResume);
    }
};

}  // anonymous namespace

void DlgSettingsGeneral::attachObserver()
{
    static ParamHandlers handlers;

    auto hDockWindows = App::GetApplication().GetUserParameter().GetGroup(
        "BaseApp/Preferences/DockWindows"
    );
    auto applyDockWidget = std::shared_ptr<ParamHandler>(new ApplyDockWidget);
    handlers.addHandler(ParamKey(hDockWindows->GetGroup("ComboView"), "Enabled"), applyDockWidget);
    handlers.addHandler(ParamKey(hDockWindows->GetGroup("TreeView"), "Enabled"), applyDockWidget);
    handlers.addHandler(ParamKey(hDockWindows->GetGroup("PropertyView"), "Enabled"), applyDockWidget);
    handlers.addHandler(ParamKey(hDockWindows->GetGroup("DAGView"), "Enabled"), applyDockWidget);
}

#include "moc_DlgSettingsGeneral.cpp"
