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

#include "VibeCADRibbon.h"

#include <algorithm>
#include <array>
#include <string_view>
#include <utility>
#include <vector>

#include <QAction>
#include <QApplication>
#include <QColor>
#include <QCompleter>
#include <QEvent>
#include <QFrame>
#include <QHBoxLayout>
#include <QHash>
#include <QKeyEvent>
#include <QLabel>
#include <QLineEdit>
#include <QMenu>
#include <QMenuBar>
#include <QResizeEvent>
#include <QSizePolicy>
#include <QStringListModel>
#include <QStyle>
#include <QTabBar>
#include <QTimer>
#include <QToolBar>
#include <QToolButton>
#include <QVBoxLayout>

#include <App/DocumentObject.h>

#include "Action.h"
#include "Application.h"
#include "Command.h"
#include "MainWindow.h"
#include "ThemeManager.h"
#include "ViewProviderDocumentObject.h"
#include "Workbench.h"
#include "WorkbenchManager.h"

namespace
{

struct DomainDefinition
{
    const char* label;
    const char* workbench;
};

constexpr std::array<DomainDefinition, 6> domains = {{
    {"Model", "PartDesignWorkbench"},
    {"Assemble", "AssemblyWorkbench"},
    {"Inspect", "InspectionWorkbench"},
    {"Analyze", "FemWorkbench"},
    {"Manufacture", "CAMWorkbench"},
    {"Drawing", "TechDrawWorkbench"},
}};

struct CommandEntry
{
    QAction* action = nullptr;
    bool separator = false;
};

using CommandEntries = std::vector<CommandEntry>;
using GroupDefinition = std::pair<QString, std::vector<QString>>;

QString sanitizedObjectName(QString value)
{
    for (int index = 0; index < value.size(); ++index) {
        if (!value.at(index).isLetterOrNumber()) {
            value[index] = QLatin1Char('_');
        }
    }
    return value;
}

QToolButton* actionButton(QAction* action, QWidget* parent)
{
    auto* button = new QToolButton(parent);
    button->setDefaultAction(action);
    button->setAutoRaise(true);
    button->setToolButtonStyle(Qt::ToolButtonIconOnly);
    button->setIconSize(QSize(20, 20));
    button->setFocusPolicy(Qt::StrongFocus);
    if (action->menu()) {
        button->setPopupMode(QToolButton::MenuButtonPopup);
    }
    return button;
}

void appendMenuEntries(QMenu* menu, const CommandEntries& entries, int skipActions = 0)
{
    int seenActions = 0;
    bool hasAction = false;
    bool separatorPending = false;

    for (const CommandEntry& entry : entries) {
        if (entry.separator) {
            if (hasAction && seenActions >= skipActions) {
                separatorPending = true;
            }
            continue;
        }
        if (!entry.action) {
            continue;
        }
        if (seenActions++ < skipActions) {
            continue;
        }
        if (separatorPending) {
            menu->addSeparator();
            separatorPending = false;
        }
        menu->addAction(entry.action);
        hasAction = true;
    }
}

int entryActionCount(const CommandEntries& entries)
{
    return static_cast<int>(std::count_if(
        entries.begin(),
        entries.end(),
        [](const CommandEntry& entry) { return entry.action != nullptr; }
    ));
}

class RibbonGroup final: public QFrame
{
public:
    RibbonGroup(QString title, CommandEntries entries, QWidget* parent = nullptr)
        : QFrame(parent)
        , _title(std::move(title))
        , _entries(std::move(entries))
    {
        setObjectName(QStringLiteral("VibeCADRibbonGroup_") + sanitizedObjectName(_title));
        setProperty("ribbonGroup", true);
        setFrameShape(QFrame::NoFrame);
        setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Fixed);

        auto* outer = new QHBoxLayout(this);
        outer->setContentsMargins(2, 0, 2, 0);
        outer->setSpacing(0);

        _expanded = new QWidget(this);
        _expanded->setObjectName(QStringLiteral("VibeCADRibbonGroupExpanded"));
        auto* expandedLayout = new QVBoxLayout(_expanded);
        expandedLayout->setContentsMargins(3, 1, 3, 1);
        expandedLayout->setSpacing(0);

        auto* commands = new QWidget(_expanded);
        auto* commandsLayout = new QHBoxLayout(commands);
        commandsLayout->setContentsMargins(0, 0, 0, 0);
        commandsLayout->setSpacing(1);

        constexpr int primaryActionCount = 4;
        int addedActions = 0;
        for (const CommandEntry& entry : _entries) {
            if (!entry.action || addedActions >= primaryActionCount) {
                continue;
            }
            commandsLayout->addWidget(actionButton(entry.action, commands));
            ++addedActions;
        }

        if (entryActionCount(_entries) > primaryActionCount) {
            auto* more = new QToolButton(commands);
            more->setObjectName(QStringLiteral("VibeCADRibbonGroupMore"));
            more->setText(QObject::tr("More"));
            more->setToolTip(QObject::tr("More %1 tools").arg(_title));
            more->setToolButtonStyle(Qt::ToolButtonTextOnly);
            more->setPopupMode(QToolButton::InstantPopup);
            more->setAutoRaise(true);
            auto* menu = new QMenu(more);
            appendMenuEntries(menu, _entries, primaryActionCount);
            more->setMenu(menu);
            commandsLayout->addWidget(more);
        }

        auto* titleLabel = new QLabel(_title, _expanded);
        titleLabel->setObjectName(QStringLiteral("VibeCADRibbonGroupTitle"));
        titleLabel->setAlignment(Qt::AlignHCenter | Qt::AlignVCenter);
        expandedLayout->addWidget(commands, 0, Qt::AlignHCenter);
        expandedLayout->addWidget(titleLabel);

        _collapsed = new QToolButton(this);
        _collapsed->setObjectName(QStringLiteral("VibeCADRibbonCollapsedGroup"));
        _collapsed->setText(_title);
        _collapsed->setToolButtonStyle(Qt::ToolButtonTextOnly);
        _collapsed->setPopupMode(QToolButton::InstantPopup);
        _collapsed->setAutoRaise(true);
        auto* collapsedMenu = new QMenu(_collapsed);
        appendMenuEntries(collapsedMenu, _entries);
        _collapsed->setMenu(collapsedMenu);

        outer->addWidget(_expanded);
        outer->addWidget(_collapsed);

        const int labelWidth = fontMetrics().horizontalAdvance(_title) + 34;
        _expandedWidth = std::max(labelWidth, addedActions * 34
                + (entryActionCount(_entries) > primaryActionCount ? 52 : 0)
                + 10);
        _collapsedWidth = std::clamp(labelWidth, 68, 120);
        setFixedHeight(56);
        setCollapsed(false);
    }

    int expandedWidth() const
    {
        return _expandedWidth;
    }

    int collapsedWidth() const
    {
        return _collapsedWidth;
    }

    const QString& title() const
    {
        return _title;
    }

    void appendCommandsTo(QMenu* menu) const
    {
        appendMenuEntries(menu, _entries);
    }

    void setCollapsed(bool collapse)
    {
        if (_isCollapsed == collapse && width() > 0) {
            return;
        }
        _isCollapsed = collapse;
        _expanded->setVisible(!collapse);
        _collapsed->setVisible(collapse);
        setProperty("collapsed", collapse);
        setFixedWidth(collapse ? _collapsedWidth : _expandedWidth);
        style()->unpolish(this);
        style()->polish(this);
    }

private:
    QString _title;
    CommandEntries _entries;
    QWidget* _expanded = nullptr;
    QToolButton* _collapsed = nullptr;
    int _expandedWidth = 0;
    int _collapsedWidth = 0;
    bool _isCollapsed = true;
};

class RibbonPage final: public QWidget
{
public:
    explicit RibbonPage(QWidget* parent = nullptr)
        : QWidget(parent)
    {
        setObjectName(QStringLiteral("VibeCADRibbonPage"));
        setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);
        setFixedHeight(58);
        _layout = new QHBoxLayout(this);
        _layout->setContentsMargins(2, 0, 2, 0);
        _layout->setSpacing(2);
        _layout->addStretch(1);
    }

    QSize minimumSizeHint() const override
    {
        return QSize(120, 58);
    }

    void setGroups(std::vector<RibbonGroup*> groups)
    {
        while (_layout->count() > 0) {
            QLayoutItem* item = _layout->takeAt(0);
            if (QWidget* widget = item->widget()) {
                delete widget;
            }
            delete item;
        }
        _groups = std::move(groups);
        for (RibbonGroup* group : _groups) {
            group->setParent(this);
            _layout->addWidget(group);
        }

        _overflow = new QToolButton(this);
        _overflow->setObjectName(QStringLiteral("VibeCADRibbonPageMore"));
        _overflow->setText(QObject::tr("More"));
        _overflow->setToolButtonStyle(Qt::ToolButtonTextUnderIcon);
        _overflow->setPopupMode(QToolButton::InstantPopup);
        _overflow->setAutoRaise(true);
        _overflow->setIcon(
            QApplication::style()->standardIcon(QStyle::SP_ToolBarHorizontalExtensionButton)
        );
        _overflow->setIconSize(QSize(20, 20));
        _overflow->setFixedSize(72, 54);
        _overflowMenu = new QMenu(_overflow);
        _overflow->setMenu(_overflowMenu);
        _overflow->hide();
        _layout->addWidget(_overflow);
        _layout->addStretch(1);
        updateCollapse();
    }

protected:
    void resizeEvent(QResizeEvent* event) override
    {
        QWidget::resizeEvent(event);
        updateCollapse();
    }

private:
    void updateCollapse()
    {
        if (_updating || _groups.empty()) {
            return;
        }
        _updating = true;

        const QMargins margins = _layout->contentsMargins();
        int required = margins.left() + margins.right()
            + std::max(0, static_cast<int>(_groups.size()) - 1) * _layout->spacing();
        _overflow->hide();
        _overflowMenu->clear();
        for (RibbonGroup* group : _groups) {
            group->show();
            group->setCollapsed(false);
            required += group->expandedWidth();
        }

        for (auto it = _groups.rbegin(); it != _groups.rend() && required > width(); ++it) {
            RibbonGroup* group = *it;
            group->setCollapsed(true);
            required -= group->expandedWidth() - group->collapsedWidth();
        }

        std::vector<RibbonGroup*> hidden;
        if (required > width()) {
            required += _overflow->width() + (_groups.empty() ? 0 : _layout->spacing());
            for (auto it = _groups.rbegin(); it != _groups.rend() && required > width(); ++it) {
                RibbonGroup* group = *it;
                group->hide();
                hidden.push_back(group);
                required -= group->collapsedWidth() + _layout->spacing();
            }
        }

        if (!hidden.empty()) {
            std::reverse(hidden.begin(), hidden.end());
            for (RibbonGroup* group : hidden) {
                QMenu* submenu = _overflowMenu->addMenu(group->title());
                group->appendCommandsTo(submenu);
            }
            _overflow->show();
        }

        _updating = false;
    }

    QHBoxLayout* _layout = nullptr;
    std::vector<RibbonGroup*> _groups;
    QToolButton* _overflow = nullptr;
    QMenu* _overflowMenu = nullptr;
    bool _updating = false;
};

std::vector<GroupDefinition> sketchGroups()
{
    return {
        {QObject::tr("Finish"),
         {"Sketcher_LeaveSketch", "Sketcher_ViewSketch", "Sketcher_ViewSection"}},
        {QObject::tr("Geometry"),
         {"Sketcher_CreatePoint",
          "Sketcher_CompLine",
          "Sketcher_CompCreateArc",
          "Sketcher_CompCreateConic",
          "Sketcher_CompCreateRectangles",
          "Sketcher_CompCreateRegularPolygon",
          "Sketcher_CompSlot",
          "Sketcher_CompCreateBSpline",
          "Sketcher_CreateText",
          "Separator",
          "Sketcher_ToggleConstruction"}},
        {QObject::tr("Constraints"),
         {"Sketcher_CompDimensionTools",
          "Separator",
          "Sketcher_ConstrainCoincidentUnified",
          "Sketcher_CompHorVer",
          "Sketcher_ConstrainParallel",
          "Sketcher_ConstrainPerpendicular",
          "Sketcher_ConstrainTangent",
          "Sketcher_ConstrainEqual",
          "Sketcher_ConstrainSymmetric",
          "Sketcher_ConstrainBlock",
          "Sketcher_ConstrainGroup",
          "Separator",
          "Sketcher_CompToggleConstraints"}},
        {QObject::tr("Modify"),
         {"Sketcher_CompCreateFillets",
          "Sketcher_CompCurveEdition",
          "Sketcher_CompExternal",
          "Sketcher_CarbonCopy",
          "Separator",
          "Sketcher_Translate",
          "Sketcher_Rotate",
          "Sketcher_Scale",
          "Sketcher_Offset",
          "Sketcher_Symmetry",
          "Sketcher_RemoveAxesAlignment"}},
        {QObject::tr("B-Spline"),
         {"Sketcher_BSplineConvertToNURBS",
          "Sketcher_BSplineIncreaseDegree",
          "Sketcher_BSplineDecreaseDegree",
          "Sketcher_CompModifyKnotMultiplicity",
          "Sketcher_BSplineInsertKnot",
          "Sketcher_JoinCurves"}},
        {QObject::tr("Visual"),
         {"Sketcher_SelectConstraints",
          "Sketcher_SelectElementsAssociatedWithConstraints",
          "Separator",
          "Sketcher_ArcOverlay",
          "Sketcher_CompBSplineShowHideGeometryInformation",
          "Sketcher_RestoreInternalAlignmentGeometry",
          "Sketcher_SwitchVirtualSpace"}},
    };
}

std::vector<GroupDefinition> inspectionGroups()
{
    return {
        {QObject::tr("Measure"), {"Std_Measure", "Std_MassProperties"}},
        {QObject::tr("Inspect"), {"Inspection_VisualInspection", "Inspection_InspectElement"}},
        {QObject::tr("Validate"), {"Part_CheckGeometry"}},
    };
}

bool isStandardToolbar(const std::string& title)
{
    static const std::array<const char*, 9> standard = {
        "File",
        "Edit",
        "Clipboard",
        "Workbench",
        "Macro",
        "View",
        "Individual Views",
        "Structure",
        "Help",
    };
    return std::find_if(standard.begin(), standard.end(), [&title](const char* item) {
               return title == item;
           })
        != standard.end();
}

QString presentationGroupTitle(QString title)
{
    static const std::array<const char*, 8> implementationPrefixes = {
        "Part Design ",
        "PartDesign ",
        "TechDraw ",
        "Sketcher ",
        "Assembly ",
        "Inspection ",
        "FEM ",
        "CAM ",
    };
    title = title.trimmed();
    for (const char* prefix : implementationPrefixes) {
        const QString candidate = QString::fromLatin1(prefix);
        if (title.startsWith(candidate, Qt::CaseInsensitive)) {
            return title.mid(candidate.size()).trimmed();
        }
    }
    return title;
}

}  // namespace

struct Gui::VibeCADRibbon::Private
{
    explicit Private(VibeCADRibbon* owner, MainWindow* window)
        : q(owner)
        , mainWindow(window)
    {}

    QAction* commandAction(const QString& commandName) const
    {
        Command* command = Application::Instance->commandManager().getCommandByName(
            commandName.toUtf8().constData()
        );
        if (!command) {
            return nullptr;
        }
        command->initAction();
        if (!command->getAction()) {
            return nullptr;
        }
        QAction* action = command->getAction()->action();
        if (action) {
            action->setProperty("VibeCADCommandId", commandName);
        }
        return action;
    }

    CommandEntries resolveEntries(const std::vector<QString>& commands) const
    {
        CommandEntries entries;
        entries.reserve(commands.size());
        for (const QString& command : commands) {
            if (command == QStringLiteral("Separator")) {
                entries.push_back({nullptr, true});
            }
            else if (QAction* action = commandAction(command)) {
                entries.push_back({action, false});
            }
        }
        return entries;
    }

    std::vector<GroupDefinition> currentWorkbenchGroups() const
    {
        Workbench* workbench = WorkbenchManager::instance()->active();
        if (!workbench) {
            return {};
        }

        std::vector<GroupDefinition> result;
        for (const auto& [title, commands] : workbench->getToolbarItems()) {
            if (isStandardToolbar(title)) {
                continue;
            }
            std::vector<QString> commandNames;
            commandNames.reserve(commands.size());
            std::transform(
                commands.begin(),
                commands.end(),
                std::back_inserter(commandNames),
                [](const std::string& command) { return QString::fromStdString(command); }
            );
            const QString displayTitle = title == "TechDraw Extend Dimensions"
                ? QObject::tr("Extend")
                : presentationGroupTitle(
                      QCoreApplication::translate("Workbench", title.c_str())
                  );
            result.emplace_back(displayTitle, std::move(commandNames));
        }
        return result;
    }

    std::vector<GroupDefinition> pageGroups() const
    {
        if (inSketchEdit) {
            return sketchGroups();
        }
        if (WorkbenchManager::instance()->activeName() == "InspectionWorkbench") {
            return inspectionGroups();
        }
        return currentWorkbenchGroups();
    }

    void rebuildPage()
    {
        std::vector<RibbonGroup*> groups;

        // View controls stay present in every CAD domain.
        CommandEntries viewEntries = resolveEntries(
            {"Std_ViewFitAll", "Std_ViewIsometric", "VibeCAD_ToggleGrid"}
        );
        if (entryActionCount(viewEntries) > 0) {
            groups.push_back(new RibbonGroup(QObject::tr("View"), std::move(viewEntries)));
        }

        for (const auto& [title, commands] : pageGroups()) {
            CommandEntries entries = resolveEntries(commands);
            if (entryActionCount(entries) > 0) {
                groups.push_back(new RibbonGroup(title, std::move(entries)));
            }
        }
        page->setGroups(std::move(groups));
    }

    void updateThemeButton() const
    {
        const ThemeManager::Mode mode = Application::Instance->themeManager()->currentMode();
        const bool dark = mode == ThemeManager::Mode::Dark;
        themeButton->setText(dark ? QObject::tr("Dark") : QObject::tr("Light"));
        themeButton->setToolTip(
            dark ? QObject::tr("Switch to Light mode") : QObject::tr("Switch to Dark mode")
        );
        themeButton->setProperty(
            "appearanceMode",
            QString::fromLatin1(ThemeManager::modeName(mode))
        );
    }

    void updateApplicationStrip(int width) const
    {
        const bool compact = width < 1050;
        for (QToolButton* button : {assistantButton, settingsButton}) {
            if (button) {
                button->setToolButtonStyle(
                    compact ? Qt::ToolButtonIconOnly : Qt::ToolButtonTextBesideIcon
                );
            }
        }
        if (assistantButton) {
            assistantButton->setText(QObject::tr("Assistant"));
        }
        if (settingsButton) {
            settingsButton->setText(QObject::tr("Settings"));
        }
    }

    void toggleTheme()
    {
        const ThemeManager::Mode current = Application::Instance->themeManager()->currentMode();
        const ThemeManager::Mode next = current == ThemeManager::Mode::Dark
            ? ThemeManager::Mode::Light
            : ThemeManager::Mode::Dark;
        Application::Instance->themeManager()->apply(next);
        updateThemeButton();
    }

    QToolButton* addCommandButton(
        QHBoxLayout* layout,
        const QString& command,
        const QString& objectName,
        bool showText
    ) const
    {
        QAction* action = commandAction(command);
        auto* button = new QToolButton(root);
        button->setObjectName(objectName);
        button->setAutoRaise(true);
        button->setIconSize(QSize(20, 20));
        if (action) {
            button->setDefaultAction(action);
            if (action->menu()) {
                button->setPopupMode(QToolButton::MenuButtonPopup);
            }
        }
        else {
            button->setEnabled(false);
            button->setText(command);
        }
        button->setToolButtonStyle(
            showText ? Qt::ToolButtonTextBesideIcon : Qt::ToolButtonIconOnly
        );
        layout->addWidget(button);
        return button;
    }

    void buildApplicationStrip(QVBoxLayout* rootLayout)
    {
        auto* strip = new QWidget(root);
        strip->setObjectName(QStringLiteral("VibeCADApplicationStrip"));
        auto* layout = new QHBoxLayout(strip);
        layout->setContentsMargins(2, 1, 2, 1);
        layout->setSpacing(2);

        appButton = new QToolButton(strip);
        appButton->setObjectName(QStringLiteral("VibeCADAppButton"));
        appButton->setText(QStringLiteral("VibeCAD"));
        appButton->setToolButtonStyle(Qt::ToolButtonTextBesideIcon);
        appButton->setPopupMode(QToolButton::InstantPopup);
        appButton->setAutoRaise(true);
        appMenu = new QMenu(appButton);
        appButton->setMenu(appMenu);
        QObject::connect(appMenu, &QMenu::aboutToShow, q, [this]() { populateAppMenu(); });
        layout->addWidget(appButton);

        addCommandButton(
            layout,
            QStringLiteral("Std_New"),
            QStringLiteral("VibeCADRibbonNew"),
            false
        );
        addCommandButton(
            layout,
            QStringLiteral("Std_Open"),
            QStringLiteral("VibeCADRibbonOpen"),
            false
        );
        addCommandButton(
            layout,
            QStringLiteral("Std_Save"),
            QStringLiteral("VibeCADRibbonSave"),
            false
        );

        auto* fileSeparator = new QFrame(strip);
        fileSeparator->setFrameShape(QFrame::VLine);
        fileSeparator->setObjectName(QStringLiteral("VibeCADRibbonSeparator"));
        layout->addWidget(fileSeparator);

        addCommandButton(
            layout,
            QStringLiteral("Std_Undo"),
            QStringLiteral("VibeCADRibbonUndo"),
            false
        );
        addCommandButton(
            layout,
            QStringLiteral("Std_Redo"),
            QStringLiteral("VibeCADRibbonRedo"),
            false
        );

        commandSearch = new QLineEdit(strip);
        commandSearch->setObjectName(QStringLiteral("VibeCADCommandSearch"));
        commandSearch->setPlaceholderText(QObject::tr("Search commands"));
        commandSearch->setClearButtonEnabled(true);
        commandSearch->setMinimumWidth(100);
        commandSearch->setMaximumWidth(360);
        commandSearch->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);
        searchModel = new QStringListModel(q);
        commandCompleter = new QCompleter(searchModel, q);
        commandCompleter->setCaseSensitivity(Qt::CaseInsensitive);
        commandCompleter->setCompletionMode(QCompleter::PopupCompletion);
        commandCompleter->setFilterMode(Qt::MatchContains);
        commandCompleter->setMaxVisibleItems(16);
        commandSearch->setCompleter(commandCompleter);
        QObject::connect(
            commandCompleter,
            qOverload<const QString&>(&QCompleter::activated),
            q,
            [this](const QString& text) { runSearchCommand(text); }
        );
        QObject::connect(commandSearch, &QLineEdit::returnPressed, q, [this]() {
            runSearchCommand(commandSearch->text());
        });
        layout->addWidget(commandSearch, 1);

        themeButton = new QToolButton(strip);
        themeButton->setObjectName(QStringLiteral("VibeCADThemeToggle"));
        themeButton->setAutoRaise(true);
        themeButton->setToolButtonStyle(Qt::ToolButtonTextOnly);
        QObject::connect(themeButton, &QToolButton::clicked, q, [this]() { toggleTheme(); });
        updateThemeButton();
        layout->addWidget(themeButton);

        assistantButton = addCommandButton(
            layout,
            QStringLiteral("VibeCAD_OpenAssistant"),
            QStringLiteral("VibeCADRibbonAssistant"),
            true
        );
        settingsButton = addCommandButton(
            layout,
            QStringLiteral("VibeCAD_OpenPreferences"),
            QStringLiteral("VibeCADRibbonSettings"),
            true
        );
        for (QToolButton* button : {assistantButton, settingsButton}) {
            if (button && button->defaultAction()) {
                QObject::connect(button->defaultAction(), &QAction::changed, q, [this]() {
                    QTimer::singleShot(0, q, [this]() { updateApplicationStrip(root->width()); });
                });
            }
        }

        rootLayout->addWidget(strip);
    }

    void buildDomainStrip(QVBoxLayout* rootLayout)
    {
        auto* strip = new QWidget(root);
        strip->setObjectName(QStringLiteral("VibeCADDomainStrip"));
        auto* layout = new QHBoxLayout(strip);
        layout->setContentsMargins(2, 0, 2, 0);
        layout->setSpacing(3);

        tabs = new QTabBar(strip);
        tabs->setObjectName(QStringLiteral("VibeCADRibbonTabs"));
        tabs->setDocumentMode(true);
        tabs->setDrawBase(false);
        tabs->setExpanding(false);
        tabs->setUsesScrollButtons(true);
        tabs->setElideMode(Qt::ElideRight);
        for (const DomainDefinition& domain : domains) {
            const int index = tabs->addTab(
                QCoreApplication::translate("VibeCADRibbon", domain.label)
            );
            tabs->setTabData(index, QString::fromLatin1(domain.workbench));
        }
        QObject::connect(tabs, &QTabBar::currentChanged, q, [this](int index) {
            activateDomain(index);
        });
        layout->addWidget(tabs);
        layout->addStretch(1);
        rootLayout->addWidget(strip);

        page = new RibbonPage(root);
        rootLayout->addWidget(page);
    }

    void build()
    {
        toolbar = new QToolBar(QObject::tr("VibeCAD Ribbon"), mainWindow);
        toolbar->setObjectName(QStringLiteral("VibeCADRibbonToolBar"));
        toolbar->setAllowedAreas(Qt::TopToolBarArea);
        toolbar->setMovable(false);
        toolbar->setFloatable(false);
        toolbar->setContextMenuPolicy(Qt::PreventContextMenu);
        toolbar->setIconSize(QSize(20, 20));
        toolbar->toggleViewAction()->setVisible(false);

        root = new QWidget(toolbar);
        root->setObjectName(QStringLiteral("VibeCADRibbon"));
        root->setMinimumWidth(0);
        root->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);
        auto* rootLayout = new QVBoxLayout(root);
        rootLayout->setContentsMargins(2, 1, 2, 1);
        rootLayout->setSpacing(0);
        buildApplicationStrip(rootLayout);
        buildDomainStrip(rootLayout);

        toolbar->addWidget(root);
        mainWindow->addToolBar(Qt::TopToolBarArea, toolbar);

        fullMenuAction = new QAction(QObject::tr("Show full menu bar"), q);
        fullMenuAction->setCheckable(true);
        QObject::connect(fullMenuAction, &QAction::toggled, q, [this](bool visible) {
            setLegacyMenuVisible(visible);
        });

        refreshSearch();
        syncDomainToWorkbench(QString::fromStdString(WorkbenchManager::instance()->activeName()));
        rebuildPage();
        updateApplicationStrip(root->width());
        enforceChrome();
    }

    void populateAppMenu()
    {
        appMenu->clear();
        for (QAction* action : mainWindow->menuBar()->actions()) {
            if (action->menu()) {
                appMenu->addAction(action);
            }
        }
        appMenu->addSeparator();
        fullMenuAction->setChecked(legacyMenuVisible);
        appMenu->addAction(fullMenuAction);
    }

    void setLegacyMenuVisible(bool visible)
    {
        legacyMenuVisible = visible;
        fullMenuAction->setChecked(visible);
        QMenuBar* menu = mainWindow->menuBar();
        menu->setVisible(visible);
        if (visible) {
            menu->setFocus(Qt::MenuBarFocusReason);
            if (!menu->actions().isEmpty()) {
                menu->setActiveAction(menu->actions().constFirst());
            }
        }
        else {
            if (QWidget* popup = QApplication::activePopupWidget()) {
                popup->close();
            }
            mainWindow->setFocus(Qt::OtherFocusReason);
        }
    }

    void enforceChrome()
    {
        for (QToolBar* candidate : mainWindow->findChildren<QToolBar*>()) {
            if (candidate == toolbar) {
                continue;
            }
            if (isMainWindowToolbar(candidate)) {
                candidate->hide();
                candidate->toggleViewAction()->setVisible(false);
            }
        }
        toolbar->toggleViewAction()->setVisible(false);
        toolbar->show();
        if (!legacyMenuVisible) {
            mainWindow->menuBar()->hide();
        }
    }

    bool isMainWindowToolbar(QToolBar* candidate) const
    {
        return candidate
            && (mainWindow->toolBarArea(candidate) != Qt::NoToolBarArea
                || candidate->parentWidget() == mainWindow);
    }

    void scheduleRefresh()
    {
        if (!refreshTimer.isActive()) {
            refreshTimer.start(0);
        }
    }

    void refresh()
    {
        refreshSearch();
        rebuildPage();
        updateThemeButton();
        updateApplicationStrip(root->width());
        enforceChrome();
    }

    void refreshSearch()
    {
        QStringList labels;
        searchCommands.clear();
        for (Command* command : Application::Instance->commandManager().getAllCommands()) {
            const QString commandId = QString::fromLatin1(command->getName());
            QString title = Action::commandMenuText(command).trimmed();
            if (title.isEmpty()) {
                title = commandId;
            }
            const QString label = QStringLiteral("%1  ·  %2").arg(title, commandId);
            labels.push_back(label);
            searchCommands.insert(label, commandId);
        }
        labels.sort(Qt::CaseInsensitive);
        searchModel->setStringList(labels);
    }

    void runSearchCommand(const QString& text)
    {
        QString commandId = searchCommands.value(text.trimmed());
        if (commandId.isEmpty()
            && Application::Instance->commandManager().getCommandByName(
                text.trimmed().toUtf8().constData()
            )) {
            commandId = text.trimmed();
        }
        if (commandId.isEmpty()) {
            return;
        }
        commandSearch->clear();
        Application::Instance->commandManager().runCommandByName(commandId.toUtf8().constData());
    }

    void activateDomain(int index)
    {
        if (syncingTabs || index < 0 || index >= tabs->count()) {
            return;
        }
        const QString workbench = tabs->tabData(index).toString();
        if (workbench.isEmpty()) {
            if (inSketchEdit) {
                rebuildPage();
            }
            return;
        }
        inSketchEdit = false;
        Application::Instance->activateWorkbench(workbench.toUtf8().constData());
        scheduleRefresh();
    }

    void syncDomainToWorkbench(const QString& workbench)
    {
        if (inSketchEdit) {
            return;
        }
        for (int index = 0; index < tabs->count(); ++index) {
            if (tabs->tabData(index).toString() == workbench) {
                syncingTabs = true;
                tabs->setCurrentIndex(index);
                syncingTabs = false;
                previousDomain = index;
                return;
            }
        }
    }

    void enterSketchEdit(const ViewProviderDocumentObject& provider)
    {
        const App::DocumentObject* object = provider.getObject();
        if (!object) {
            return;
        }
        const std::string_view typeName = object->getTypeId().getName();
        if (!typeName.starts_with("Sketcher::SketchObject")) {
            return;
        }
        inSketchEdit = true;
        previousDomain = tabs->currentIndex();
        int sketchIndex = -1;
        for (int index = 0; index < tabs->count(); ++index) {
            if (tabs->tabData(index).toString().isEmpty()) {
                sketchIndex = index;
                break;
            }
        }
        if (sketchIndex < 0) {
            sketchIndex = tabs->addTab(QObject::tr("Sketch"));
            tabs->setTabData(sketchIndex, QString());
            tabs->setTabTextColor(sketchIndex, QColor(QStringLiteral("#4dabf7")));
        }
        syncingTabs = true;
        tabs->setCurrentIndex(sketchIndex);
        syncingTabs = false;
        rebuildPage();
        QTimer::singleShot(0, q, [this]() { enforceChrome(); });
    }

    void leaveSketchEdit()
    {
        if (!inSketchEdit) {
            return;
        }
        inSketchEdit = false;
        for (int index = tabs->count() - 1; index >= 0; --index) {
            if (tabs->tabData(index).toString().isEmpty()) {
                tabs->removeTab(index);
            }
        }
        syncingTabs = true;
        tabs->setCurrentIndex(std::clamp(previousDomain, 0, tabs->count() - 1));
        syncingTabs = false;
        rebuildPage();
        QTimer::singleShot(0, q, [this]() { enforceChrome(); });
    }

    VibeCADRibbon* q;
    MainWindow* mainWindow;
    QToolBar* toolbar = nullptr;
    QWidget* root = nullptr;
    QToolButton* appButton = nullptr;
    QMenu* appMenu = nullptr;
    QAction* fullMenuAction = nullptr;
    QLineEdit* commandSearch = nullptr;
    QStringListModel* searchModel = nullptr;
    QCompleter* commandCompleter = nullptr;
    QHash<QString, QString> searchCommands;
    QToolButton* themeButton = nullptr;
    QToolButton* assistantButton = nullptr;
    QToolButton* settingsButton = nullptr;
    QTabBar* tabs = nullptr;
    RibbonPage* page = nullptr;
    QTimer refreshTimer;
    bool syncingTabs = false;
    bool inSketchEdit = false;
    bool legacyMenuVisible = false;
    bool pendingAltToggle = false;
    int previousDomain = 0;
    fastsignals::scoped_connection commandsChanged;
    fastsignals::scoped_connection enteredEdit;
    fastsignals::scoped_connection leftEdit;
};

Gui::VibeCADRibbon* Gui::VibeCADRibbon::install(MainWindow* mainWindow)
{
    if (!mainWindow) {
        return nullptr;
    }
    if (QObject* existing = mainWindow->findChild<QObject*>(
            QStringLiteral("VibeCADRibbonController"),
            Qt::FindDirectChildrenOnly
        )) {
        return dynamic_cast<VibeCADRibbon*>(existing);
    }
    return new VibeCADRibbon(mainWindow);
}

Gui::VibeCADRibbon::VibeCADRibbon(MainWindow* mainWindow)
    : QObject(mainWindow)
    , d(std::make_unique<Private>(this, mainWindow))
{
    setObjectName(QStringLiteral("VibeCADRibbonController"));
    d->refreshTimer.setSingleShot(true);
    d->refreshTimer.setParent(this);
    connect(&d->refreshTimer, &QTimer::timeout, this, [this]() { d->refresh(); });
    connect(mainWindow, &MainWindow::workbenchActivated, this, [this](const QString& workbench) {
        d->syncDomainToWorkbench(workbench);
        d->scheduleRefresh();
    });
    connect(
        Application::Instance->themeManager(),
        &ThemeManager::modeChanged,
        this,
        [this]() { d->updateThemeButton(); }
    );

    d->commandsChanged = Application::Instance->commandManager().signalChanged.connect(
        [this]() { d->scheduleRefresh(); }
    );
    d->enteredEdit = Application::Instance->signalInEdit.connect(
        [this](const ViewProviderDocumentObject& provider) { d->enterSketchEdit(provider); }
    );
    d->leftEdit = Application::Instance->signalResetEdit.connect(
        [this](const ViewProviderDocumentObject&) { d->leaveSketchEdit(); }
    );

    qApp->installEventFilter(this);
    d->build();
}

Gui::VibeCADRibbon::~VibeCADRibbon()
{
    if (qApp) {
        qApp->removeEventFilter(this);
    }
}

bool Gui::VibeCADRibbon::eventFilter(QObject* watched, QEvent* event)
{
    if (event->type() == QEvent::KeyPress) {
        auto* keyEvent = static_cast<QKeyEvent*>(event);
        if (!keyEvent->isAutoRepeat() && keyEvent->key() == Qt::Key_Alt) {
            d->pendingAltToggle = true;
            return true;
        }
        if (keyEvent->key() != Qt::Key_Alt) {
            d->pendingAltToggle = false;
        }
        if (!keyEvent->isAutoRepeat() && keyEvent->key() == Qt::Key_F10) {
            d->setLegacyMenuVisible(!d->legacyMenuVisible);
            return true;
        }
    }
    else if (event->type() == QEvent::KeyRelease) {
        auto* keyEvent = static_cast<QKeyEvent*>(event);
        if (!keyEvent->isAutoRepeat() && keyEvent->key() == Qt::Key_Alt) {
            const bool toggle = d->pendingAltToggle;
            d->pendingAltToggle = false;
            if (toggle) {
                d->setLegacyMenuVisible(!d->legacyMenuVisible);
            }
            return toggle;
        }
    }
    else if (event->type() == QEvent::Show) {
        if (auto* toolbar = qobject_cast<QToolBar*>(watched)) {
            if (toolbar != d->toolbar && d->isMainWindowToolbar(toolbar)) {
                QTimer::singleShot(0, this, [this]() { d->enforceChrome(); });
            }
        }
        else if (watched == d->mainWindow->menuBar() && !d->legacyMenuVisible) {
            QTimer::singleShot(0, this, [this]() { d->enforceChrome(); });
        }
    }
    else if (event->type() == QEvent::LanguageChange && watched == qApp) {
        d->scheduleRefresh();
    }
    else if (event->type() == QEvent::Resize && watched == d->root) {
        d->updateApplicationStrip(static_cast<QResizeEvent*>(event)->size().width());
    }
    return QObject::eventFilter(watched, event);
}
