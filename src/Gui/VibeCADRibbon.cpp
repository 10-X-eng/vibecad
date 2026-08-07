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
#include <QIcon>
#include <QKeySequence>
#include <QLineEdit>
#include <QList>
#include <QMenu>
#include <QMenuBar>
#include <QMdiArea>
#include <QMdiSubWindow>
#include <QPointer>
#include <QResizeEvent>
#include <QSet>
#include <QSignalBlocker>
#include <QSizePolicy>
#include <QStringListModel>
#include <QStyle>
#include <QTabBar>
#include <QTimer>
#include <QToolBar>
#include <QToolButton>
#include <QVBoxLayout>
#include <QWidgetAction>

#include <App/DocumentObject.h>

#include "Action.h"
#include "Application.h"
#include "BitmapFactory.h"
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

constexpr std::array<DomainDefinition, 7> domains = {{
    {"Model", "PartDesignWorkbench"},
    {"Assemble", "AssemblyWorkbench"},
    {"Mesh", "MeshWorkbench"},
    {"Analyze", "FemWorkbench"},
    {"Manufacture", "CAMWorkbench"},
    {"Drawing", "TechDrawWorkbench"},
    {"Parameters", "SpreadsheetWorkbench"},
}};

struct CommandEntry
{
    QAction* action = nullptr;
    bool separator = false;
    QList<QAction*> childActions;
    Gui::ActionGroup* actionGroup = nullptr;
    bool ownedAction = false;
};

using CommandEntries = std::vector<CommandEntry>;
using GroupDefinition = std::pair<QString, std::vector<QString>>;

QString actionCommandId(const QAction* action)
{
    if (!action) {
        return {};
    }
    QString commandId = action->property("VibeCADCommandId").toString().trimmed();
    if (commandId.isEmpty()) {
        commandId = action->property("CommandName").toString().trimmed();
    }
    if (commandId.isEmpty()) {
        commandId = action->property("FreeCADCommandGroupChildId").toString().trimmed();
    }
    if (commandId.isEmpty()) {
        commandId = action->objectName().trimmed();
    }
    return commandId;
}

QString accessibleActionName(const QAction* action, const QString& commandId)
{
    QString name = action ? action->text() : QString();
    name.remove(QLatin1Char('&'));
    name = name.trimmed();
    return name.isEmpty() ? commandId : name;
}

QIcon commandFallbackIcon(const QString& commandId)
{
    const QByteArray encoded = commandId.toUtf8();
    QIcon icon = Gui::BitmapFactory().iconFromTheme(encoded.constData());
    if (icon.isNull()) {
        icon = QIcon::fromTheme(commandId);
    }
    if (icon.isNull()) {
        icon = QIcon(QStringLiteral(":/icons/%1.svg").arg(commandId));
    }
    return icon;
}

void ensureActionPresentation(QAction* action, const QString& commandId, bool unavailable = false)
{
    if (!action) {
        return;
    }

    action->setProperty("VibeCADCommandId", commandId);
    action->setProperty("VibeCADUnavailable", unavailable);
    if (action->text().trimmed().isEmpty()) {
        action->setText(unavailable ? QObject::tr("%1 (Unavailable)").arg(commandId) : commandId);
    }

    const QString accessibleName = accessibleActionName(action, commandId);
    action->setProperty("VibeCADAccessibleName", accessibleName);
    if (action->toolTip().trimmed().isEmpty()) {
        action->setToolTip(
            unavailable ? QObject::tr("%1 is unavailable in this build.").arg(commandId) : accessibleName
        );
    }
    if (action->statusTip().trimmed().isEmpty()) {
        action->setStatusTip(action->toolTip());
    }

    bool missingIcon = action->property("VibeCADMissingIcon").toBool();
    if (action->icon().isNull()) {
        QIcon fallback = commandFallbackIcon(commandId);
        if (fallback.isNull()) {
            missingIcon = true;
            fallback = QApplication::style()->standardIcon(QStyle::SP_MessageBoxWarning);
        }
        action->setIcon(fallback);
    }
    action->setProperty("VibeCADMissingIcon", missingIcon);
    if (unavailable) {
        action->setEnabled(false);
    }
}

void decorateCompositeWrapper(QAction* wrapper, const CommandEntry& entry)
{
    if (!wrapper || !entry.action) {
        return;
    }
    const QPointer<QAction> source(entry.action);
    const QPointer<QAction> target(wrapper);
    const auto synchronize = [source, target]() {
        if (!source || !target) {
            return;
        }
        target->setText(source->text());
        target->setIcon(source->icon());
        target->setEnabled(source->isEnabled());
        target->setVisible(source->isVisible());
        target->setProperty("VibeCADCommandId", actionCommandId(source));
        target->setProperty("VibeCADComposite", true);
        target->setProperty("VibeCADUnavailable", source->property("VibeCADUnavailable"));
        target->setProperty("VibeCADMissingIcon", source->property("VibeCADMissingIcon"));
        target->setProperty("VibeCADAccessibleName", source->property("VibeCADAccessibleName"));
        target->setToolTip(source->toolTip());
        target->setStatusTip(source->statusTip());
    };
    synchronize();
    QObject::connect(entry.action, &QAction::changed, wrapper, synchronize);
}

QString sanitizedObjectName(QString value)
{
    for (int index = 0; index < value.size(); ++index) {
        if (!value.at(index).isLetterOrNumber()) {
            value[index] = QLatin1Char('_');
        }
    }
    return value;
}

void connectActionGroupMenu(QMenu* menu, Gui::ActionGroup* actionGroup)
{
    if (!menu || !actionGroup) {
        return;
    }
    QObject::connect(menu, &QMenu::aboutToShow, actionGroup, [actionGroup, menu]() {
        Q_EMIT actionGroup->aboutToShow(menu);
    });
    QObject::connect(menu, &QMenu::aboutToHide, actionGroup, [actionGroup, menu]() {
        Q_EMIT actionGroup->aboutToHide(menu);
    });
}

QToolButton* actionButton(const CommandEntry& entry, QWidget* parent)
{
    auto* button = new QToolButton(parent);
    button->setDefaultAction(entry.action);
    button->setProperty("ribbonCommand", true);
    button->setProperty("VibeCADCommandId", actionCommandId(entry.action));
    button->setProperty("VibeCADUnavailable", entry.action->property("VibeCADUnavailable"));
    button->setProperty("VibeCADMissingIcon", entry.action->property("VibeCADMissingIcon"));
    button->setAccessibleName(entry.action->property("VibeCADAccessibleName").toString());
    button->setToolTip(entry.action->toolTip());
    button->setAutoRaise(true);
    button->setToolButtonStyle(Qt::ToolButtonIconOnly);
    button->setIconSize(QSize(28, 28));
    button->setFocusPolicy(Qt::StrongFocus);
    if (!entry.childActions.isEmpty()) {
        auto* menu = new QMenu(button);
        menu->addActions(entry.childActions);
        connectActionGroupMenu(menu, entry.actionGroup);
        button->setMenu(menu);
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
        if (entry.childActions.isEmpty()) {
            menu->addAction(entry.action);
        }
        else {
            auto* submenu = menu->addMenu(entry.action->icon(), entry.action->text());
            decorateCompositeWrapper(submenu->menuAction(), entry);
            submenu->addActions(entry.childActions);
            connectActionGroupMenu(submenu, entry.actionGroup);
        }
        hasAction = true;
    }
}

int entryActionCount(const CommandEntries& entries)
{
    return static_cast<int>(std::count_if(entries.begin(), entries.end(), [](const CommandEntry& entry) {
        return entry.action != nullptr;
    }));
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
        for (CommandEntry& entry : _entries) {
            if (entry.ownedAction && entry.action && !entry.action->parent()) {
                entry.action->setParent(this);
            }
        }
        for (const CommandEntry& entry : _entries) {
            if (!entry.action || addedActions >= primaryActionCount) {
                continue;
            }
            commandsLayout->addWidget(actionButton(entry, commands));
            ++addedActions;
        }

        expandedLayout->addWidget(commands, 0, Qt::AlignHCenter);

        auto* groupMenu = new QToolButton(_expanded);
        groupMenu->setObjectName(QStringLiteral("VibeCADRibbonGroupMenu"));
        groupMenu->setText(_title.toUpper());
        groupMenu->setToolTip(QObject::tr("Open all %1 tools").arg(_title));
        groupMenu->setToolButtonStyle(Qt::ToolButtonTextOnly);
        groupMenu->setPopupMode(QToolButton::InstantPopup);
        groupMenu->setAutoRaise(true);
        auto* groupMenuEntries = new QMenu(groupMenu);
        appendMenuEntries(groupMenuEntries, _entries);
        groupMenu->setMenu(groupMenuEntries);
        expandedLayout->addWidget(groupMenu);

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

        const int labelWidth = fontMetrics().horizontalAdvance(_title.toUpper()) + 30;
        _expandedWidth = std::max(labelWidth, addedActions * 42 + 12);
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
        // Layout changes can synchronously deliver resize/layout events. Stop
        // updateCollapse() from traversing the previous page while its widgets
        // are being destroyed, and release every stale observer before the
        // first child is deleted.
        _updating = true;
        _groups.clear();
        _overflow = nullptr;
        _overflowMenu = nullptr;
        while (_layout->count() > 0) {
            QLayoutItem* item = _layout->takeAt(0);
            if (QWidget* widget = item->widget()) {
                delete widget;
            }
            delete item;
        }
        _groups = std::move(groups);
        int groupOrder = 0;
        for (RibbonGroup* group : _groups) {
            group->setParent(this);
            group->setProperty("ribbonOrder", groupOrder++);
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
        _updating = false;
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
         {"Sketcher_LeaveSketch", "Sketcher_CancelSketch", "Sketcher_ViewSketch", "Sketcher_ViewSection"}},
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

std::vector<GroupDefinition> sketchSetupGroups()
{
    return {
        {QObject::tr("Sketch"),
         {"Sketcher_NewSketch",
          "Sketcher_EditSketch",
          "Sketcher_MapSketch",
          "Sketcher_ReorientSketch",
          "Sketcher_ValidateSketch",
          "Sketcher_MergeSketches",
          "Sketcher_MirrorSketch"}},
    };
}

const std::vector<GroupDefinition>& surfaceGroups()
{
    static const std::vector<GroupDefinition> groups = {
        {QObject::tr("Surface"),
         {"Surface_Filling",
          "Surface_GeomFillSurface",
          "Surface_Sections",
          "Surface_ExtendFace",
          "Surface_CurveOnMesh",
          "Surface_BlendCurve"}},
    };
    return groups;
}

const std::vector<GroupDefinition>& pointsGroups()
{
    static const std::vector<GroupDefinition> groups = {
        {QObject::tr("Points"),
         {"Points_Import",
          "Points_Export",
          "Separator",
          "Points_Convert",
          "Points_Structure",
          "Points_Merge",
          "Points_PolyCut"}},
    };
    return groups;
}

const std::vector<GroupDefinition>& reverseEngineeringGroups()
{
    static const std::vector<GroupDefinition> groups = {
        {QObject::tr("Rebuild"), {"Reen_PoissonReconstruction", "Reen_ViewTriangulation"}},
        {QObject::tr("Segment"),
         {"Reen_Segmentation",
          "Reen_SegmentationManual",
          "Reen_SegmentationFromComponents",
          "Reen_MeshBoundary"}},
        {QObject::tr("Approximate"),
         {"Reen_ApproxPlane",
          "Reen_ApproxCylinder",
          "Reen_ApproxSphere",
          "Reen_ApproxPolynomial",
          "Separator",
          "Reen_ApproxSurface",
          "Reen_ApproxCurve"}},
    };
    return groups;
}

const std::vector<GroupDefinition>& spreadsheetGroups()
{
    static const std::vector<GroupDefinition> groups = {
        {QObject::tr("Sheet"),
         {"Spreadsheet_CreateSheet", "Spreadsheet_Import", "Spreadsheet_Export"}},
        {QObject::tr("Cells"),
         {"Spreadsheet_MergeCells",
          "Spreadsheet_SplitCell",
          "Spreadsheet_CellProperties",
          "Spreadsheet_SetAlias"}},
        {QObject::tr("Align"),
         {"Spreadsheet_AlignLeft",
          "Spreadsheet_AlignCenter",
          "Spreadsheet_AlignRight",
          "Spreadsheet_AlignTop",
          "Spreadsheet_AlignVCenter",
          "Spreadsheet_AlignBottom"}},
        {QObject::tr("Style"),
         {"Spreadsheet_StyleBold", "Spreadsheet_StyleItalic", "Spreadsheet_StyleUnderline"}},
    };
    return groups;
}

const std::vector<GroupDefinition>& robotAssemblyGroups()
{
    static const std::vector<GroupDefinition> groups = {
        {QObject::tr("Robot"),
         {"Robot_Create", "Robot_AddToolShape", "Robot_SetDefaultOrientation", "Robot_SetDefaultValues"}},
        {QObject::tr("Trajectory"),
         {"Robot_CreateTrajectory",
          "Robot_InsertWaypoint",
          "Robot_InsertWaypointPreselect",
          "Robot_Edge2Trac",
          "Robot_TrajectoryDressUp",
          "Robot_TrajectoryCompound"}},
        {QObject::tr("Motion"), {"Robot_SetHomePos", "Robot_RestoreHomePos", "Robot_Simulate"}},
    };
    return groups;
}

const std::vector<GroupDefinition>& robotManufactureGroups()
{
    static const std::vector<GroupDefinition> groups = {
        {QObject::tr("Robot"),
         {"Robot_Edge2Trac", "Robot_TrajectoryDressUp", "Robot_TrajectoryCompound", "Robot_Simulate"}},
        {QObject::tr("Export"), {"Robot_ExportKukaCompact", "Robot_ExportKukaFull"}},
    };
    return groups;
}

void mergeGroups(std::vector<GroupDefinition>& destination, const std::vector<GroupDefinition>& source)
{
    for (const auto& [title, commands] : source) {
        auto existing = std::find_if(
            destination.begin(),
            destination.end(),
            [&title](const GroupDefinition& group) { return group.first == title; }
        );
        if (existing == destination.end()) {
            destination.emplace_back(title, commands);
            continue;
        }

        auto& existingCommands = existing->second;
        bool separatorPending = !existingCommands.empty();
        for (const QString& command : commands) {
            if (command == QStringLiteral("Separator")) {
                separatorPending = true;
                continue;
            }
            if (std::find(existingCommands.begin(), existingCommands.end(), command)
                != existingCommands.end()) {
                continue;
            }
            if (separatorPending && !existingCommands.empty()
                && existingCommands.back() != QStringLiteral("Separator")) {
                existingCommands.push_back(QStringLiteral("Separator"));
            }
            existingCommands.push_back(command);
            separatorPending = false;
        }
    }
}

const std::vector<QString>& sharedInspectionCommands()
{
    static const std::vector<QString> commands = {
        QStringLiteral("Std_Measure"),
        QStringLiteral("Std_MassProperties"),
        QStringLiteral("Inspection_VisualInspection"),
        QStringLiteral("Inspection_InspectElement"),
        QStringLiteral("Part_CheckGeometry"),
    };
    return commands;
}

const std::vector<GroupDefinition>& componentInterfaceGroups()
{
    static const std::vector<GroupDefinition> groups = {
        {QObject::tr("Connect"), {"VibeCAD_PublishInterface"}},
    };
    return groups;
}

bool isSharedInspectionCommand(const QString& command)
{
    const std::vector<QString>& commands = sharedInspectionCommands();
    return std::find(commands.begin(), commands.end(), command) != commands.end();
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
    return std::find_if(
               standard.begin(),
               standard.end(),
               [&title](const char* item) { return title == item; }
           )
        != standard.end();
}

QString presentationGroupTitle(const std::string& implementationTitle)
{
    static const std::array<std::pair<const char*, const char*>, 26> groupTitles = {{
        {"Part Design Helper Features", QT_TRANSLATE_NOOP("VibeCADRibbon", "Structure")},
        {"Create and Remove Material", QT_TRANSLATE_NOOP("VibeCADRibbon", "Solids")},
        {"Finish Shape", QT_TRANSLATE_NOOP("VibeCADRibbon", "Finish")},
        {"Transform Features", QT_TRANSLATE_NOOP("VibeCADRibbon", "Transform")},
        {"Standalone and Surface Geometry", QT_TRANSLATE_NOOP("VibeCADRibbon", "Geometry")},
        {"Boolean, Split, and Repair", QT_TRANSLATE_NOOP("VibeCADRibbon", "Modify")},
        {"Standard Components", QT_TRANSLATE_NOOP("VibeCADRibbon", "Fasteners")},
        {"Electromagnetic Boundary Conditions",
         QT_TRANSLATE_NOOP("VibeCADRibbon", "Electromagnetics")},
        {"Fluid Boundary Conditions", QT_TRANSLATE_NOOP("VibeCADRibbon", "Fluids")},
        {"Geometrical Analysis Features", QT_TRANSLATE_NOOP("VibeCADRibbon", "Geometry")},
        {"Mechanical Boundary Conditions and Loads", QT_TRANSLATE_NOOP("VibeCADRibbon", "Mechanics")},
        {"Thermal Boundary Conditions and Loads", QT_TRANSLATE_NOOP("VibeCADRibbon", "Thermal")},
        {"Project Setup", QT_TRANSLATE_NOOP("VibeCADRibbon", "Setup")},
        {"Tool Commands", QT_TRANSLATE_NOOP("VibeCADRibbon", "Tools")},
        {"New Operations", QT_TRANSLATE_NOOP("VibeCADRibbon", "Operations")},
        {"Path Modification", QT_TRANSLATE_NOOP("VibeCADRibbon", "Modify")},
        {"Helpful Tools", QT_TRANSLATE_NOOP("VibeCADRibbon", "Area")},
        {"TechDraw Extend Dimensions", QT_TRANSLATE_NOOP("VibeCADRibbon", "Extend")},
        {"TechDraw File Access", QT_TRANSLATE_NOOP("VibeCADRibbon", "Files")},
        {"Mesh Tools", QT_TRANSLATE_NOOP("VibeCADRibbon", "Tools")},
        {"Mesh Convert", QT_TRANSLATE_NOOP("VibeCADRibbon", "Convert")},
        {"Mesh Modify", QT_TRANSLATE_NOOP("VibeCADRibbon", "Modify")},
        {"Mesh Boolean", QT_TRANSLATE_NOOP("VibeCADRibbon", "Boolean")},
        {"Mesh Cutting", QT_TRANSLATE_NOOP("VibeCADRibbon", "Cut")},
        {"Mesh Segmentation", QT_TRANSLATE_NOOP("VibeCADRibbon", "Segment")},
        {"Mesh Analyze", QT_TRANSLATE_NOOP("VibeCADRibbon", "Analyze")},
    }};
    for (const auto& [sourceTitle, presentationTitle] : groupTitles) {
        if (implementationTitle == sourceTitle) {
            return QCoreApplication::translate("VibeCADRibbon", presentationTitle);
        }
    }

    QString title = QCoreApplication::translate("Workbench", implementationTitle.c_str());
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

    CommandEntry commandEntry(const QString& commandName, bool useToolBarPresentation = false) const
    {
        Command* command = Application::Instance->commandManager().getCommandByName(
            commandName.toUtf8().constData()
        );
        if (!command) {
            auto* unavailable = new QAction();
            ensureActionPresentation(unavailable, commandName, true);
            return {unavailable, false, {}, nullptr, true};
        }
        command->initAction();
        if (!command->getAction()) {
            auto* unavailable = new QAction();
            ensureActionPresentation(unavailable, commandName, true);
            return {unavailable, false, {}, nullptr, true};
        }
        QAction* action = command->getAction()->action();
        if (useToolBarPresentation) {
            if (auto* undo = dynamic_cast<UndoAction*>(command->getAction())) {
                action = undo->toolBarAction();
            }
            else if (auto* redo = dynamic_cast<RedoAction*>(command->getAction())) {
                action = redo->toolBarAction();
            }
        }
        if (!action) {
            auto* unavailable = new QAction();
            ensureActionPresentation(unavailable, commandName, true);
            return {unavailable, false, {}, nullptr, true};
        }
        ensureActionPresentation(action, commandName);
        auto* actionGroup = dynamic_cast<ActionGroup*>(command->getAction());
        QList<QAction*> childActions = actionGroup ? actionGroup->actions() : QList<QAction*>();
        int childIndex = 0;
        for (QAction* child : childActions) {
            if (!child || child->isSeparator()) {
                ++childIndex;
                continue;
            }
            QString childCommandId = actionCommandId(child);
            const QString nativeParentId
                = child->property("FreeCADCommandGroupParentId").toString().trimmed();
            const QVariant nativeIndex = child->property("FreeCADCommandGroupChildIndex");
            const bool validGroupChild = nativeParentId == commandName && nativeIndex.isValid()
                && nativeIndex.toInt() == childIndex;
            const bool validSyntheticChild = child->property("FreeCADCommandGroupSynthetic").toBool()
                && validGroupChild;
            bool unavailable = false;
            if (childCommandId.isEmpty()) {
                childCommandId = QStringLiteral("%1/child-%2").arg(commandName).arg(childIndex + 1);
                unavailable = true;
            }
            else if (
                !validGroupChild
                && !Application::Instance->commandManager().getCommandByName(
                    childCommandId.toUtf8().constData()
                )
            ) {
                unavailable = true;
            }
            ensureActionPresentation(child, childCommandId, unavailable);
            child->setProperty(
                "VibeCADParentCommandId",
                nativeParentId.isEmpty() ? commandName : nativeParentId
            );
            child->setProperty(
                "VibeCADCompositeChildIndex",
                nativeIndex.isValid() ? nativeIndex : QVariant(childIndex)
            );
            child->setProperty("VibeCADGroupCommandChild", validGroupChild);
            child->setProperty("VibeCADSyntheticCommand", validSyntheticChild);
            ++childIndex;
        }
        return {
            action,
            false,
            std::move(childActions),
            actionGroup,
            false,
        };
    }

    QAction* commandAction(
        const QString& commandName,
        bool* ownedAction = nullptr,
        bool useToolBarPresentation = false
    ) const
    {
        CommandEntry entry = commandEntry(commandName, useToolBarPresentation);
        if (ownedAction) {
            *ownedAction = entry.ownedAction;
        }
        return entry.action;
    }

    ActionGroup* commandActionGroup(const QString& commandName) const
    {
        Command* command = Application::Instance->commandManager().getCommandByName(
            commandName.toUtf8().constData()
        );
        if (!command) {
            return nullptr;
        }
        command->initAction();
        return dynamic_cast<ActionGroup*>(command->getAction());
    }

    CommandEntries resolveEntries(const std::vector<QString>& commands) const
    {
        CommandEntries entries;
        entries.reserve(commands.size());
        for (const QString& command : commands) {
            if (command == QStringLiteral("Separator")) {
                entries.push_back({nullptr, true, {}, nullptr});
            }
            else if (CommandEntry entry = commandEntry(command); entry.action) {
                entries.push_back(std::move(entry));
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
            const QString displayTitle = presentationGroupTitle(title);
            result.emplace_back(displayTitle, std::move(commandNames));
        }
        return result;
    }

    std::vector<GroupDefinition> pageGroups() const
    {
        if (inSketchEdit) {
            return sketchGroups();
        }
        const std::string activeWorkbench = WorkbenchManager::instance()->activeName();
        if (activeWorkbench == "SketcherWorkbench") {
            return sketchSetupGroups();
        }
        if (activeWorkbench == "SpreadsheetWorkbench") {
            return spreadsheetGroups();
        }

        std::vector<GroupDefinition> groups = currentWorkbenchGroups();
        const auto appendComposed =
            [&groups](const char* workbench, const std::vector<GroupDefinition>& additions) {
                if (Application::Instance->initializeWorkbench(workbench)) {
                    mergeGroups(groups, additions);
                }
            };

        if (activeWorkbench == "PartDesignWorkbench") {
            appendComposed("SurfaceWorkbench", surfaceGroups());
            mergeGroups(groups, componentInterfaceGroups());
        }
        else if (activeWorkbench == "MeshWorkbench") {
            appendComposed("PointsWorkbench", pointsGroups());
            appendComposed("ReverseEngineeringWorkbench", reverseEngineeringGroups());
        }
        else if (activeWorkbench == "AssemblyWorkbench") {
            appendComposed("RobotWorkbench", robotAssemblyGroups());
            mergeGroups(groups, componentInterfaceGroups());
        }
        else if (activeWorkbench == "CAMWorkbench") {
            appendComposed("RobotWorkbench", robotManufactureGroups());
        }
        return groups;
    }

    void rebuildPage()
    {
        std::vector<RibbonGroup*> groups;
        bool inspectionAdded = false;
        QSet<QString> surfacedActionIds;

        const auto resolveUniqueEntries =
            [this, &surfacedActionIds](const std::vector<QString>& commands) {
                CommandEntries entries;
                entries.reserve(commands.size());
                for (const QString& command : commands) {
                    if (command == QStringLiteral("Separator")) {
                        entries.push_back({nullptr, true, {}, nullptr});
                    }
                    else if (!surfacedActionIds.contains(command)) {
                        CommandEntry entry = commandEntry(command);
                        surfacedActionIds.insert(command);
                        QList<QAction*> uniqueChildren;
                        uniqueChildren.reserve(entry.childActions.size());
                        for (QAction* child : std::as_const(entry.childActions)) {
                            if (!child) {
                                continue;
                            }
                            if (child->isSeparator()) {
                                uniqueChildren.push_back(child);
                                continue;
                            }
                            const QString childId = actionCommandId(child);
                            if (!surfacedActionIds.contains(childId)) {
                                surfacedActionIds.insert(childId);
                                uniqueChildren.push_back(child);
                            }
                        }
                        entry.childActions = std::move(uniqueChildren);
                        entries.push_back(std::move(entry));
                    }
                }
                return entries;
            };

        const auto addInspectionGroup = [&groups, &inspectionAdded, &resolveUniqueEntries]() {
            if (inspectionAdded) {
                return;
            }
            inspectionAdded = true;
            CommandEntries entries = resolveUniqueEntries(sharedInspectionCommands());
            if (entryActionCount(entries) > 0) {
                groups.push_back(new RibbonGroup(QObject::tr("Inspect"), std::move(entries)));
            }
        };

        // View controls stay present in every CAD domain.
        CommandEntries viewEntries = resolveUniqueEntries(
            {"Std_ViewFitAll", "Std_ViewIsometric", "VibeCAD_ToggleGrid"}
        );
        if (entryActionCount(viewEntries) > 0) {
            groups.push_back(new RibbonGroup(QObject::tr("View"), std::move(viewEntries)));
        }

        for (const auto& [title, commands] : pageGroups()) {
            if (!inSketchEdit && title == QObject::tr("Fasteners")) {
                addInspectionGroup();
            }

            std::vector<QString> domainCommands = commands;
            if (!inSketchEdit) {
                std::erase_if(domainCommands, isSharedInspectionCommand);
            }
            CommandEntries entries = resolveUniqueEntries(domainCommands);
            if (entryActionCount(entries) > 0) {
                groups.push_back(new RibbonGroup(title, std::move(entries)));
            }
        }
        if (!inSketchEdit && !inspectionAdded) {
            addInspectionGroup();
        }
        page->setGroups(std::move(groups));
    }

    void updateThemeButton() const
    {
        if (!themeButton) {
            return;
        }
        const ThemeManager::Mode mode = Application::Instance->themeManager()->currentMode();
        const bool dark = mode == ThemeManager::Mode::Dark;
        themeButton->setText(QString());
        themeButton->setIcon(QIcon(QStringLiteral(":/icons/Std_SetAppearance.svg")));
        themeButton->setToolTip(
            dark ? QObject::tr("Switch to Light mode") : QObject::tr("Switch to Dark mode")
        );
        themeButton->setAccessibleName(
            dark ? QObject::tr("Dark appearance") : QObject::tr("Light appearance")
        );
        themeButton->setProperty("appearanceMode", QString::fromLatin1(ThemeManager::modeName(mode)));
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
        const QString& menuCommand = {}
    ) const
    {
        auto* button = new QToolButton(root);
        bool ownedAction = false;
        QAction* action = commandAction(command, &ownedAction, true);
        button->setObjectName(objectName);
        button->setAutoRaise(true);
        button->setIconSize(QSize(20, 20));
        if (action) {
            if (ownedAction) {
                action->setParent(button);
            }
            if (action->menu()) {
                button->setMenu(action->menu());
                button->setPopupMode(QToolButton::MenuButtonPopup);
            }
            button->setDefaultAction(action);
            button->setProperty("VibeCADCommandId", actionCommandId(action));
            button->setProperty("VibeCADUnavailable", action->property("VibeCADUnavailable"));
            button->setProperty("VibeCADMissingIcon", action->property("VibeCADMissingIcon"));
            button->setAccessibleName(action->property("VibeCADAccessibleName").toString());
            button->setToolTip(action->toolTip());
        }
        else {
            button->setEnabled(false);
            button->setText(command);
        }
        if (!menuCommand.isEmpty()) {
            ActionGroup* menuActionGroup = commandActionGroup(menuCommand);
            const QList<QAction*> menuActions
                = menuActionGroup ? menuActionGroup->actions() : QList<QAction*>();
            if (!menuActions.isEmpty()) {
                auto* menu = new QMenu(button);
                menu->addActions(menuActions);
                connectActionGroupMenu(menu, menuActionGroup);
                button->setMenu(menu);
                button->setPopupMode(QToolButton::MenuButtonPopup);
                button->setProperty("VibeCADMenuCommandId", menuCommand);
            }
        }
        button->setToolButtonStyle(Qt::ToolButtonIconOnly);
        layout->addWidget(button);
        return button;
    }

    void buildApplicationStrip(QVBoxLayout* rootLayout)
    {
        auto* strip = new QWidget(root);
        strip->setObjectName(QStringLiteral("VibeCADApplicationStrip"));
        auto* layout = new QHBoxLayout(strip);
        layout->setContentsMargins(4, 2, 4, 2);
        layout->setSpacing(4);

        auto* leadingTools = new QWidget(strip);
        leadingTools->setObjectName(QStringLiteral("VibeCADLeadingTools"));
        auto* leadingLayout = new QHBoxLayout(leadingTools);
        leadingLayout->setContentsMargins(0, 0, 0, 0);
        leadingLayout->setSpacing(1);

        appButton = new QToolButton(leadingTools);
        appButton->setObjectName(QStringLiteral("VibeCADAppButton"));
        appButton->setIcon(QIcon(QStringLiteral(":/icons/vibecad.svg")));
        appButton->setIconSize(QSize(24, 24));
        appButton->setToolTip(QObject::tr("VibeCAD menu"));
        appButton->setAccessibleName(QObject::tr("VibeCAD menu"));
        appButton->setToolButtonStyle(Qt::ToolButtonIconOnly);
        appButton->setPopupMode(QToolButton::InstantPopup);
        appButton->setAutoRaise(true);
        appMenu = new QMenu(appButton);
        appButton->setMenu(appMenu);
        QObject::connect(appMenu, &QMenu::aboutToShow, q, [this]() { populateAppMenu(); });
        leadingLayout->addWidget(appButton);

        addCommandButton(
            leadingLayout,
            QStringLiteral("Std_Open"),
            QStringLiteral("VibeCADRibbonOpen"),
            QStringLiteral("Std_RecentFiles")
        );
        addCommandButton(leadingLayout, QStringLiteral("Std_Save"), QStringLiteral("VibeCADRibbonSave"));

        auto* fileSeparator = new QFrame(strip);
        fileSeparator->setFrameShape(QFrame::VLine);
        fileSeparator->setObjectName(QStringLiteral("VibeCADRibbonSeparator"));
        leadingLayout->addWidget(fileSeparator);

        addCommandButton(leadingLayout, QStringLiteral("Std_Undo"), QStringLiteral("VibeCADRibbonUndo"));
        addCommandButton(leadingLayout, QStringLiteral("Std_Redo"), QStringLiteral("VibeCADRibbonRedo"));
        layout->addWidget(leadingTools);

        documentTabs = new QTabBar(strip);
        documentTabs->setObjectName(QStringLiteral("VibeCADDocumentTabs"));
        documentTabs->setDocumentMode(true);
        documentTabs->setDrawBase(false);
        documentTabs->setTabsClosable(true);
        documentTabs->setMovable(true);
        documentTabs->setExpanding(true);
        documentTabs->setUsesScrollButtons(true);
        documentTabs->setElideMode(Qt::ElideMiddle);
        documentTabs->setIconSize(QSize(16, 16));
        documentTabs->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);
        QObject::connect(documentTabs, &QTabBar::currentChanged, q, [this](int index) {
            activateDocumentTab(index);
        });
        QObject::connect(documentTabs, &QTabBar::tabCloseRequested, q, [this](int index) {
            closeDocumentTab(index);
        });
        QObject::connect(documentTabs, &QTabBar::tabMoved, q, [this](int from, int to) {
            moveDocumentTab(from, to);
        });
        layout->addWidget(documentTabs, 1);

        newDocumentButton
            = addCommandButton(layout, QStringLiteral("Std_New"), QStringLiteral("VibeCADRibbonNew"));
        if (newDocumentButton) {
            newDocumentButton->setIcon(QIcon(QStringLiteral(":/icons/list-add.svg")));
            newDocumentButton->setToolTip(QObject::tr("New document"));
        }

        auto* trailingTools = new QWidget(strip);
        trailingTools->setObjectName(QStringLiteral("VibeCADTrailingTools"));
        auto* trailingLayout = new QHBoxLayout(trailingTools);
        trailingLayout->setContentsMargins(0, 0, 0, 0);
        trailingLayout->setSpacing(1);

        searchButton = new QToolButton(trailingTools);
        searchButton->setObjectName(QStringLiteral("VibeCADRibbonSearch"));
        searchButton->setIcon(
            QIcon::fromTheme(
                QStringLiteral("edit-find"),
                QIcon(QStringLiteral(":/icons/zoom-selection.svg"))
            )
        );
        searchButton->setIconSize(QSize(18, 18));
        searchButton->setToolTip(QObject::tr("Search commands (Ctrl+K)"));
        searchButton->setAccessibleName(QObject::tr("Search commands"));
        searchButton->setAutoRaise(true);
        searchButton->setPopupMode(QToolButton::InstantPopup);

        searchMenu = new QMenu(searchButton);
        searchMenu->setObjectName(QStringLiteral("VibeCADCommandSearchMenu"));
        auto* searchPanel = new QWidget(searchMenu);
        auto* searchLayout = new QHBoxLayout(searchPanel);
        searchLayout->setContentsMargins(8, 7, 8, 7);
        searchLayout->setSpacing(0);

        commandSearch = new QLineEdit(searchPanel);
        commandSearch->setObjectName(QStringLiteral("VibeCADCommandSearch"));
        commandSearch->setPlaceholderText(QObject::tr("Search commands"));
        commandSearch->setClearButtonEnabled(true);
        commandSearch->setMinimumWidth(320);
        commandSearch->setMaximumWidth(420);
        searchLayout->addWidget(commandSearch);
        auto* searchWidgetAction = new QWidgetAction(searchMenu);
        searchWidgetAction->setDefaultWidget(searchPanel);
        searchMenu->addAction(searchWidgetAction);
        searchButton->setMenu(searchMenu);

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
        QObject::connect(searchMenu, &QMenu::aboutToShow, q, [this]() {
            QTimer::singleShot(0, commandSearch, [this]() {
                commandSearch->setFocus(Qt::ShortcutFocusReason);
                commandSearch->selectAll();
            });
        });
        trailingLayout->addWidget(searchButton);

        themeButton = new QToolButton(trailingTools);
        themeButton->setObjectName(QStringLiteral("VibeCADThemeToggle"));
        themeButton->setAutoRaise(true);
        themeButton->setIconSize(QSize(18, 18));
        themeButton->setToolButtonStyle(Qt::ToolButtonIconOnly);
        QObject::connect(themeButton, &QToolButton::clicked, q, [this]() { toggleTheme(); });
        updateThemeButton();
        trailingLayout->addWidget(themeButton);

        assistantButton = addCommandButton(
            trailingLayout,
            QStringLiteral("VibeCAD_OpenAssistant"),
            QStringLiteral("VibeCADRibbonAssistant")
        );
        settingsButton = addCommandButton(
            trailingLayout,
            QStringLiteral("VibeCAD_OpenPreferences"),
            QStringLiteral("VibeCADRibbonSettings")
        );
        layout->addWidget(trailingTools);

        searchShortcut = new QAction(QObject::tr("Search commands"), q);
        searchShortcut->setShortcut(QKeySequence(QStringLiteral("Ctrl+K")));
        searchShortcut->setShortcutContext(Qt::ApplicationShortcut);
        mainWindow->addAction(searchShortcut);
        QObject::connect(searchShortcut, &QAction::triggered, q, [this]() {
            if (searchButton) {
                searchButton->showMenu();
            }
        });

        rootLayout->addWidget(strip);
        attachDocumentTabs();
    }

    void scheduleDocumentTabsSync()
    {
        if (!documentTabsSyncTimer.isActive()) {
            documentTabsSyncTimer.start(0);
        }
    }

    void observeDocumentWindows()
    {
        QMdiArea* mdiArea = mainWindow->getMdiArea();
        if (!mdiArea) {
            return;
        }
        for (QMdiSubWindow* subWindow : mdiArea->subWindowList(QMdiArea::CreationOrder)) {
            if (!subWindow || observedDocumentWindows.contains(subWindow)) {
                continue;
            }
            observedDocumentWindows.insert(subWindow);
            QObject::connect(subWindow, &QWidget::windowTitleChanged, q, [this]() {
                scheduleDocumentTabsSync();
            });
            QObject::connect(subWindow, &QWidget::windowIconChanged, q, [this]() {
                scheduleDocumentTabsSync();
            });
            QObject::connect(subWindow, &QObject::destroyed, q, [this, subWindow]() {
                observedDocumentWindows.remove(subWindow);
                scheduleDocumentTabsSync();
            });
        }
    }

    void syncDocumentTabs()
    {
        if (!documentTabs || !sourceDocumentTabs) {
            return;
        }

        collapseSourceDocumentTabs();
        observeDocumentWindows();
        syncingDocumentTabs = true;

        while (documentTabs->count() > sourceDocumentTabs->count()) {
            documentTabs->removeTab(documentTabs->count() - 1);
        }
        while (documentTabs->count() < sourceDocumentTabs->count()) {
            documentTabs->addTab(QString());
        }

        const QIcon fallbackIcon(QStringLiteral(":/icons/Document.svg"));
        for (int index = 0; index < sourceDocumentTabs->count(); ++index) {
            documentTabs->setTabText(index, sourceDocumentTabs->tabText(index));
            const QIcon icon = sourceDocumentTabs->tabIcon(index);
            documentTabs->setTabIcon(index, icon.isNull() ? fallbackIcon : icon);
            documentTabs->setTabToolTip(index, sourceDocumentTabs->tabToolTip(index));
            documentTabs->setTabEnabled(index, sourceDocumentTabs->isTabEnabled(index));
            documentTabs->setTabData(index, sourceDocumentTabs->tabData(index));
        }
        documentTabs->setCurrentIndex(sourceDocumentTabs->currentIndex());
        syncingDocumentTabs = false;
    }

    void collapseSourceDocumentTabs()
    {
        if (!sourceDocumentTabs) {
            return;
        }
        if (!sourceDocumentTabsGeometryCaptured) {
            sourceDocumentTabsMinimumHeight = sourceDocumentTabs->minimumHeight();
            sourceDocumentTabsMaximumHeight = sourceDocumentTabs->maximumHeight();
            sourceDocumentTabsStyleSheet = sourceDocumentTabs->styleSheet();
            sourceDocumentTabsGeometryCaptured = true;

            const QString separator = sourceDocumentTabsStyleSheet.isEmpty() ? QString()
                                                                             : QStringLiteral("\n");
            sourceDocumentTabs->setStyleSheet(
                sourceDocumentTabsStyleSheet + separator
                + QStringLiteral(
                    "QTabBar#mdiAreaTabBar, QTabBar#mdiAreaTabBar::tab {"
                    " min-width: 0px; max-width: 0px; width: 0px;"
                    " min-height: 0px; max-height: 0px; height: 0px;"
                    " padding: 0px; margin: 0px; border: 0px;"
                    "}"
                    "QTabBar#mdiAreaTabBar::close-button {"
                    " width: 0px; height: 0px;"
                    " padding: 0px; margin: 0px; border: 0px;"
                    "}"
                )
            );

            // QMdiArea derives its viewport margins directly from the tab
            // bar's sizeHint(). Refresh its private layout after changing that
            // hint so the reclaimed space is available immediately.
            if (QMdiArea* mdiArea = mainWindow->getMdiArea()) {
                const bool documentMode = mdiArea->documentMode();
                mdiArea->setDocumentMode(!documentMode);
                mdiArea->setDocumentMode(documentMode);
            }
        }

        // Retain the source bar as the MDI controller for activation, close,
        // and reorder operations while its ribbon mirror owns presentation.
        sourceDocumentTabs->setFixedHeight(0);
        sourceDocumentTabs->hide();
        sourceDocumentTabs->updateGeometry();
    }

    void restoreSourceDocumentTabs()
    {
        if (!sourceDocumentTabs) {
            return;
        }
        if (sourceDocumentTabsGeometryCaptured) {
            sourceDocumentTabs->setStyleSheet(sourceDocumentTabsStyleSheet);
            sourceDocumentTabs->setMinimumHeight(sourceDocumentTabsMinimumHeight);
            sourceDocumentTabs->setMaximumHeight(sourceDocumentTabsMaximumHeight);
            if (QMdiArea* mdiArea = mainWindow->getMdiArea()) {
                const bool documentMode = mdiArea->documentMode();
                mdiArea->setDocumentMode(!documentMode);
                mdiArea->setDocumentMode(documentMode);
            }
        }
        sourceDocumentTabs->show();
        sourceDocumentTabs->updateGeometry();
    }

    void attachDocumentTabs()
    {
        QMdiArea* mdiArea = mainWindow->getMdiArea();
        if (!mdiArea) {
            return;
        }
        sourceDocumentTabs = mdiArea->findChild<QTabBar*>(QStringLiteral("mdiAreaTabBar"));
        if (!sourceDocumentTabs) {
            return;
        }

        sourceDocumentTabs->installEventFilter(q);
        mdiArea->installEventFilter(q);
        mdiArea->viewport()->installEventFilter(q);
        QObject::connect(sourceDocumentTabs, &QTabBar::currentChanged, q, [this](int) {
            scheduleDocumentTabsSync();
        });
        QObject::connect(sourceDocumentTabs, &QTabBar::tabMoved, q, [this](int, int) {
            scheduleDocumentTabsSync();
        });
        QObject::connect(mdiArea, &QMdiArea::subWindowActivated, q, [this](QMdiSubWindow*) {
            scheduleDocumentTabsSync();
        });
        collapseSourceDocumentTabs();
        syncDocumentTabs();
    }

    void activateDocumentTab(int index)
    {
        if (syncingDocumentTabs || !sourceDocumentTabs || index < 0
            || index >= sourceDocumentTabs->count()) {
            return;
        }
        sourceDocumentTabs->setCurrentIndex(index);
    }

    void closeDocumentTab(int index)
    {
        if (!sourceDocumentTabs || index < 0 || index >= sourceDocumentTabs->count()) {
            return;
        }

        const bool invoked = QMetaObject::invokeMethod(
            sourceDocumentTabs,
            "tabCloseRequested",
            Qt::DirectConnection,
            Q_ARG(int, index)
        );
        if (!invoked) {
            sourceDocumentTabs->setCurrentIndex(index);
            if (QMdiSubWindow* active = mainWindow->getMdiArea()->activeSubWindow()) {
                active->close();
            }
        }
        scheduleDocumentTabsSync();
    }

    void moveDocumentTab(int from, int to)
    {
        if (syncingDocumentTabs || !sourceDocumentTabs || from == to || from < 0 || to < 0
            || from >= sourceDocumentTabs->count() || to >= sourceDocumentTabs->count()) {
            return;
        }
        syncingDocumentTabs = true;
        sourceDocumentTabs->moveTab(from, to);
        syncingDocumentTabs = false;
        scheduleDocumentTabsSync();
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
            const int index = tabs->addTab(QCoreApplication::translate("VibeCADRibbon", domain.label));
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
        fullMenuAction->setChecked(legacyMenuVisible);
        QObject::connect(fullMenuAction, &QAction::toggled, q, [this](bool visible) {
            setLegacyMenuVisible(visible);
        });

        refreshSearch();
        syncDomainToWorkbench(QString::fromStdString(WorkbenchManager::instance()->activeName()));
        rebuildPage();
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
        collapseSourceDocumentTabs();
        mainWindow->menuBar()->setVisible(legacyMenuVisible);
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
        syncDocumentTabs();
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
        if (searchMenu) {
            searchMenu->close();
        }
        Application::Instance->commandManager().runCommandByName(commandId.toUtf8().constData());
    }

    int sketchTabIndex() const
    {
        for (int index = 0; index < tabs->count(); ++index) {
            if (tabs->tabData(index).toString().isEmpty()) {
                return index;
            }
        }
        return -1;
    }

    void setDomainTabsEnabled(bool enabled)
    {
        const QSignalBlocker blocker(tabs);
        for (int index = 0; index < tabs->count(); ++index) {
            if (!tabs->tabData(index).toString().isEmpty()) {
                tabs->setTabEnabled(index, enabled);
            }
        }
    }

    int showSketchTab()
    {
        int sketchIndex = sketchTabIndex();
        const QSignalBlocker blocker(tabs);
        syncingTabs = true;
        if (sketchIndex < 0) {
            if (tabs->currentIndex() >= 0) {
                previousDomain = tabs->currentIndex();
            }
            sketchIndex = tabs->addTab(QObject::tr("Sketch"));
            tabs->setTabData(sketchIndex, QString());
            tabs->setTabTextColor(sketchIndex, QColor(QStringLiteral("#4dabf7")));
        }
        tabs->setCurrentIndex(sketchIndex);
        syncingTabs = false;
        return sketchIndex;
    }

    void removeSketchTabAndSelect(int targetIndex)
    {
        const QSignalBlocker blocker(tabs);
        syncingTabs = true;
        for (int index = tabs->count() - 1; index >= 0; --index) {
            if (tabs->tabData(index).toString().isEmpty()) {
                tabs->removeTab(index);
            }
        }
        if (tabs->count() > 0) {
            tabs->setCurrentIndex(std::clamp(targetIndex, 0, tabs->count() - 1));
        }
        syncingTabs = false;
    }

    void activateDomain(int index)
    {
        if (syncingTabs || index < 0 || index >= tabs->count()) {
            return;
        }
        const QString workbench = tabs->tabData(index).toString();
        if (workbench.isEmpty()) {
            rebuildPage();
            return;
        }
        if (inSketchEdit) {
            showSketchTab();
            return;
        }
        Application::Instance->activateWorkbench(workbench.toUtf8().constData());
        scheduleRefresh();
    }

    void syncDomainToWorkbench(const QString& workbench)
    {
        if (workbench == QStringLiteral("SketcherWorkbench")) {
            showSketchTab();
            setDomainTabsEnabled(!inSketchEdit);
            return;
        }
        if (inSketchEdit) {
            return;
        }
        int targetIndex = previousDomain;
        for (int index = 0; index < tabs->count(); ++index) {
            if (tabs->tabData(index).toString() == workbench) {
                targetIndex = index;
                previousDomain = index;
                break;
            }
        }
        setDomainTabsEnabled(true);
        removeSketchTabAndSelect(targetIndex);
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
        showSketchTab();
        setDomainTabsEnabled(false);
        rebuildPage();
        QTimer::singleShot(0, q, [this]() { enforceChrome(); });
    }

    void leaveSketchEdit()
    {
        if (!inSketchEdit) {
            return;
        }
        inSketchEdit = false;
        setDomainTabsEnabled(true);
        syncDomainToWorkbench(QString::fromStdString(WorkbenchManager::instance()->activeName()));
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
    QTabBar* documentTabs = nullptr;
    QPointer<QTabBar> sourceDocumentTabs;
    int sourceDocumentTabsMinimumHeight = 0;
    int sourceDocumentTabsMaximumHeight = QWIDGETSIZE_MAX;
    QString sourceDocumentTabsStyleSheet;
    bool sourceDocumentTabsGeometryCaptured = false;
    QToolButton* newDocumentButton = nullptr;
    QLineEdit* commandSearch = nullptr;
    QStringListModel* searchModel = nullptr;
    QCompleter* commandCompleter = nullptr;
    QHash<QString, QString> searchCommands;
    QToolButton* searchButton = nullptr;
    QMenu* searchMenu = nullptr;
    QAction* searchShortcut = nullptr;
    QToolButton* themeButton = nullptr;
    QToolButton* assistantButton = nullptr;
    QToolButton* settingsButton = nullptr;
    QTabBar* tabs = nullptr;
    RibbonPage* page = nullptr;
    QTimer refreshTimer;
    QTimer documentTabsSyncTimer;
    QSet<QMdiSubWindow*> observedDocumentWindows;
    bool syncingTabs = false;
    bool syncingDocumentTabs = false;
    bool inSketchEdit = false;
    bool legacyMenuVisible = true;
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
    d->documentTabsSyncTimer.setSingleShot(true);
    d->documentTabsSyncTimer.setParent(this);
    connect(&d->documentTabsSyncTimer, &QTimer::timeout, this, [this]() { d->syncDocumentTabs(); });
    connect(mainWindow, &MainWindow::workbenchActivated, this, [this](const QString& workbench) {
        d->syncDomainToWorkbench(workbench);
        d->scheduleRefresh();
    });
    connect(Application::Instance->themeManager(), &ThemeManager::modeChanged, this, [this]() {
        d->updateThemeButton();
    });

    d->commandsChanged = Application::Instance->commandManager().signalChanged.connect([this]() {
        d->scheduleRefresh();
    });
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
    if (d->sourceDocumentTabs) {
        d->sourceDocumentTabs->removeEventFilter(this);
        d->restoreSourceDocumentTabs();
    }
}

bool Gui::VibeCADRibbon::eventFilter(QObject* watched, QEvent* event)
{
    QMdiArea* mdiArea = d->mainWindow->getMdiArea();
    const bool isDocumentChrome = watched == d->sourceDocumentTabs || watched == mdiArea
        || (mdiArea && watched == mdiArea->viewport());
    if (isDocumentChrome
        && (event->type() == QEvent::ChildAdded || event->type() == QEvent::ChildRemoved
            || event->type() == QEvent::LayoutRequest || event->type() == QEvent::UpdateRequest)) {
        d->scheduleDocumentTabsSync();
    }

    if (event->type() == QEvent::Show) {
        if (watched == d->sourceDocumentTabs) {
            QTimer::singleShot(0, this, [this]() { d->collapseSourceDocumentTabs(); });
        }
        else if (auto* toolbar = qobject_cast<QToolBar*>(watched)) {
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
    return QObject::eventFilter(watched, event);
}
