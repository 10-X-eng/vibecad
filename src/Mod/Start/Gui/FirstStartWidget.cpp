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


#include <QGuiApplication>
#include <QFrame>
#include <QHBoxLayout>
#include <QIcon>
#include <QLabel>
#include <QPushButton>
#include <QResizeEvent>
#include <QVBoxLayout>
#include <QWidget>


#include "FirstStartWidget.h"
#include "ThemeSelectorWidget.h"
#include "GeneralSettingsWidget.h"

#include <App/Application.h>
#include <gsl/pointers>

using namespace StartGui;

FirstStartWidget::FirstStartWidget(QWidget* parent)
    : QGroupBox(parent)
    , _themeSelectorWidget {nullptr}
    , _generalSettingsWidget {nullptr}
    , _welcomeLabel {nullptr}
    , _descriptionLabel {nullptr}
    , _aiTitleLabel {nullptr}
    , _aiDescriptionLabel {nullptr}
    , _personalizeLabel {nullptr}
    , _configureAIButton {nullptr}
    , _openAssistantButton {nullptr}
    , _doneButton {nullptr}
{
    setObjectName(QLatin1String("FirstStartWidget"));
    setMaximumWidth(1180);
    setupUi();
    qApp->installEventFilter(this);
}

void FirstStartWidget::setupUi()
{
    auto outerLayout = gsl::owner<QVBoxLayout*>(new QVBoxLayout(this));
    outerLayout->setContentsMargins(32, 28, 32, 28);
    outerLayout->setSpacing(18);

    auto headerLayout = gsl::owner<QHBoxLayout*>(new QHBoxLayout);
    headerLayout->setSpacing(18);

    auto mark = gsl::owner<QLabel*>(new QLabel);
    mark->setObjectName(QLatin1String("VibeCADFirstStartMark"));
    mark->setPixmap(QIcon(QLatin1String(":/icons/vibecad.svg")).pixmap(72, 72));
    mark->setFixedSize(72, 72);
    headerLayout->addWidget(mark, 0, Qt::AlignTop);

    auto welcomeLayout = gsl::owner<QVBoxLayout*>(new QVBoxLayout);
    welcomeLayout->setSpacing(4);
    _welcomeLabel = gsl::owner<QLabel*>(new QLabel);
    _welcomeLabel->setObjectName(QLatin1String("VibeCADFirstStartTitle"));
    welcomeLayout->addWidget(_welcomeLabel);
    _descriptionLabel = gsl::owner<QLabel*>(new QLabel);
    _descriptionLabel->setObjectName(QLatin1String("VibeCADFirstStartDescription"));
    _descriptionLabel->setWordWrap(true);
    welcomeLayout->addWidget(_descriptionLabel);
    headerLayout->addLayout(welcomeLayout, 1);
    outerLayout->addLayout(headerLayout);

    auto aiCard = gsl::owner<QFrame*>(new QFrame);
    aiCard->setObjectName(QLatin1String("VibeCADAISetupCard"));
    auto aiCardLayout = gsl::owner<QVBoxLayout*>(new QVBoxLayout(aiCard));
    aiCardLayout->setContentsMargins(20, 18, 20, 18);
    aiCardLayout->setSpacing(18);

    auto aiTextLayout = gsl::owner<QVBoxLayout*>(new QVBoxLayout);
    aiTextLayout->setSpacing(4);
    _aiTitleLabel = gsl::owner<QLabel*>(new QLabel);
    _aiTitleLabel->setObjectName(QLatin1String("VibeCADAISetupTitle"));
    aiTextLayout->addWidget(_aiTitleLabel);
    _aiDescriptionLabel = gsl::owner<QLabel*>(new QLabel);
    _aiDescriptionLabel->setObjectName(QLatin1String("VibeCADAISetupDescription"));
    _aiDescriptionLabel->setWordWrap(true);
    aiTextLayout->addWidget(_aiDescriptionLabel);
    aiCardLayout->addLayout(aiTextLayout, 1);

    auto aiButtonLayout = gsl::owner<QHBoxLayout*>(new QHBoxLayout);
    aiButtonLayout->addStretch();
    _configureAIButton = gsl::owner<QPushButton*>(new QPushButton);
    _configureAIButton->setObjectName(QLatin1String("VibeCADFirstStartConfigureAI"));
    _configureAIButton->setProperty("vibeStartPrimary", true);
    _configureAIButton->setIcon(QIcon(QLatin1String(":/icons/preferences-general.svg")));
    connect(
        _configureAIButton,
        &QPushButton::clicked,
        this,
        &FirstStartWidget::configureAIRequested
    );
    aiButtonLayout->addWidget(_configureAIButton);

    _openAssistantButton = gsl::owner<QPushButton*>(new QPushButton);
    _openAssistantButton->setObjectName(QLatin1String("VibeCADFirstStartOpenAssistant"));
    _openAssistantButton->setIcon(QIcon(QLatin1String(":/icons/vibecad.svg")));
    connect(
        _openAssistantButton,
        &QPushButton::clicked,
        this,
        &FirstStartWidget::openAssistantRequested
    );
    aiButtonLayout->addWidget(_openAssistantButton);
    aiCardLayout->addLayout(aiButtonLayout);
    outerLayout->addWidget(aiCard);

    _personalizeLabel = gsl::owner<QLabel*>(new QLabel);
    _personalizeLabel->setObjectName(QLatin1String("VibeCADFirstStartSectionTitle"));
    outerLayout->addWidget(_personalizeLabel);

    _themeSelectorWidget = gsl::owner<ThemeSelectorWidget*>(new ThemeSelectorWidget(this));
    _generalSettingsWidget = gsl::owner<GeneralSettingsWidget*>(new GeneralSettingsWidget(this));

    outerLayout->addWidget(_generalSettingsWidget);
    outerLayout->addWidget(_themeSelectorWidget);

    _doneButton = gsl::owner<QPushButton*>(new QPushButton);
    _doneButton->setObjectName(QLatin1String("VibeCADFirstStartContinue"));
    connect(_doneButton, &QPushButton::clicked, this, &FirstStartWidget::dismissed);
    auto buttonBar = gsl::owner<QHBoxLayout*>(new QHBoxLayout);
    buttonBar->setAlignment(Qt::AlignRight);
    buttonBar->addWidget(_doneButton);
    outerLayout->addLayout(buttonBar);

    retranslateUi();
}

bool FirstStartWidget::eventFilter(QObject* object, QEvent* event)
{
    if (object == this && event->type() == QEvent::LanguageChange) {
        this->retranslateUi();
    }
    return QWidget::eventFilter(object, event);
}

void FirstStartWidget::retranslateUi()
{
    _doneButton->setText(tr("Continue to Start"));
    _welcomeLabel->setText(tr("Welcome to VibeCAD"));
    _descriptionLabel->setText(
        tr("Set up your AI collaborator and make the workspace yours. You can change every "
           "option later in Preferences.")
    );
    _aiTitleLabel->setText(tr("1. Connect your AI"));
    _aiDescriptionLabel->setText(
        tr("Use a ChatGPT subscription, OpenAI or Anthropic API key, or an X / Grok account. "
           "VibeCAD keeps sign-in and provider settings in its existing secure setup flow.")
    );
    _configureAIButton->setText(tr("Set up AI"));
    _openAssistantButton->setText(tr("Open Assistant"));
    _personalizeLabel->setText(tr("2. Personalize your workspace"));
}
