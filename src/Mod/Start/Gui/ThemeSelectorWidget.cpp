// SPDX-License-Identifier: LGPL-2.1-or-later
/****************************************************************************
 *                                                                          *
 *   Copyright (c) 2024 The FreeCAD Project Association AISBL               *
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

#include <QApplication>
#include <QEvent>
#include <QHBoxLayout>
#include <QLabel>
#include <QString>
#include <QToolButton>

#include <map>

#include "ThemeSelectorWidget.h"
#include <gsl/pointers>
#include <Gui/Application.h>
#include <Gui/ThemeManager.h>

using namespace StartGui;


ThemeSelectorWidget::ThemeSelectorWidget(QWidget* parent)
    : QWidget(parent)
    , _titleLabel {nullptr}
    , _buttons {nullptr, nullptr}
{
    setObjectName(QLatin1String("ThemeSelectorWidget"));
    setupUi();
    connect(
        Gui::Application::Instance->themeManager(),
        &Gui::ThemeManager::modeChanged,
        this,
        [this](Gui::ThemeManager::Mode mode) {
            const Theme selected =
                mode == Gui::ThemeManager::Mode::Light ? Theme::Light : Theme::Dark;
            _buttons[static_cast<int>(selected)]->setChecked(true);
        }
    );
    qApp->installEventFilter(this);
}


void ThemeSelectorWidget::setupButtons(QBoxLayout* layout)
{
    if (!layout) {
        return;
    }
    const std::map<Theme, QString> themeMap {
        {Theme::Light, tr("Light")},
        {Theme::Dark, tr("Dark")},
    };
    std::map<Theme, QIcon> iconMap {
        {Theme::Light, QIcon(QLatin1String(":/thumbnails/Theme_thumbnail_light.png"))},
        {Theme::Dark, QIcon(QLatin1String(":/thumbnails/Theme_thumbnail_dark.png"))},
    };
    const Gui::ThemeManager::Mode selected =
        Gui::Application::Instance->themeManager()->currentMode();
    for (const auto& theme : themeMap) {
        auto button = gsl::owner<QToolButton*>(new QToolButton());
        button->setCheckable(true);
        button->setAutoExclusive(true);
        button->setToolButtonStyle(Qt::ToolButtonStyle::ToolButtonTextUnderIcon);
        button->setText(theme.second);
        button->setIcon(iconMap[theme.first]);
        button->setIconSize(iconMap[theme.first].actualSize(QSize(256, 256)));
        button->setChecked(
            (theme.first == Theme::Light && selected == Gui::ThemeManager::Mode::Light)
            || (theme.first == Theme::Dark && selected == Gui::ThemeManager::Mode::Dark)
        );
        connect(button, &QToolButton::clicked, this, [this, theme] { themeChanged(theme.first); });
        layout->addWidget(button);
        _buttons[static_cast<int>(theme.first)] = button;
    }
}

void ThemeSelectorWidget::setupUi()
{
    auto* outerLayout = gsl::owner<QVBoxLayout*>(new QVBoxLayout(this));
    auto* buttonLayout = gsl::owner<QHBoxLayout*>(new QHBoxLayout);
    _titleLabel = gsl::owner<QLabel*>(new QLabel);
    outerLayout->addWidget(_titleLabel);
    outerLayout->addLayout(buttonLayout);
    setupButtons(buttonLayout);
    retranslateUi();
}

void ThemeSelectorWidget::themeChanged(Theme newTheme)
{
    Gui::Application::Instance->themeManager()->apply(
        newTheme == Theme::Light ? Gui::ThemeManager::Mode::Light
                                 : Gui::ThemeManager::Mode::Dark
    );
}

bool ThemeSelectorWidget::eventFilter(QObject* object, QEvent* event)
{
    if (object == this && event->type() == QEvent::LanguageChange) {
        this->retranslateUi();
    }
    return QWidget::eventFilter(object, event);
}

void ThemeSelectorWidget::retranslateUi()
{
    _titleLabel->setText(QLatin1String("<h2>") + tr("Theme") + QLatin1String("</h2>"));
    _buttons[static_cast<int>(Theme::Light)]->setText(tr("Light", "Visual theme name"));
    _buttons[static_cast<int>(Theme::Dark)]->setText(tr("Dark", "Visual theme name"));
}
