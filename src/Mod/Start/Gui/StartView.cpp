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
#include <QCheckBox>
#include <QFrame>
#include <QGridLayout>
#include <QHBoxLayout>
#include <QIcon>
#include <QLabel>
#include <QListView>
#include <QMdiSubWindow>
#include <QMessageBox>
#include <QPushButton>
#include <QScrollArea>
#include <QTimer>
#include <QWidget>
#include <QStackedWidget>
#include <QShowEvent>

#include "StartView.h"
#include "FileCardDelegate.h"
#include "FileCardView.h"
#include "FirstStartWidget.h"
#include "FlowLayout.h"
#include "NewFileButton.h"
#include <App/DocumentObject.h>
#include <App/Application.h>
#include <Base/Interpreter.h>
#include <Base/Tools.h>
#include <Gui/Action.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/Document.h>
#include <Gui/MainWindow.h>
#include <Gui/ModuleIO.h>
#include <Gui/View3DInventor.h>
#include <Gui/View3DInventorViewer.h>
#include <gsl/pointers>
#include <string>

using namespace StartGui;

TYPESYSTEM_SOURCE_ABSTRACT(StartGui::StartView, Gui::MDIView)  // NOLINT


StartView::StartView(QWidget* parent)
    : Gui::MDIView(nullptr, parent)
    , _contents(new QStackedWidget(this))
    , _newFileLabel {nullptr}
    , _heroTitleLabel {nullptr}
    , _heroDescriptionLabel {nullptr}
    , _examplesLabel {nullptr}
    , _recentFilesLabel {nullptr}
    , _customFolderLabel {nullptr}
    , _configureAI {nullptr}
    , _openAssistant {nullptr}
    , _showOnStartupCheckBox {nullptr}
{
    setObjectName(QLatin1String("StartView"));
    // Start is a full-width document surface. The permanent model browser is
    // useful for CAD views, but would otherwise sit invisibly above the left
    // side of this page and intercept its first card in every section.
    setProperty("vibecadUsesModelBrowser", false);
    auto hGrp = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/Mod/Start"
    );
    auto cardSpacing = hGrp->GetInt("FileCardSpacing", 15);  // NOLINT
    auto showExamples = hGrp->GetBool("ShowExamples", true);

    // Verify that the folder specified in preferences is available before showing it
    std::string customFolder(hGrp->GetASCII("CustomFolder", ""));
    bool showCustomFolder = false;
    if (!customFolder.empty()) {
        showCustomFolder = true;
    }

    // First start page
    auto firstStartScrollArea = gsl::owner<QScrollArea*>(new QScrollArea());
    auto firstStartScrollWidget = gsl::owner<QWidget*>(new QWidget(firstStartScrollArea));
    firstStartScrollArea->setWidget(firstStartScrollWidget);
    firstStartScrollArea->setWidgetResizable(true);

    auto firstStartRegion = gsl::owner<QHBoxLayout*>(new QHBoxLayout(firstStartScrollWidget));
    firstStartRegion->setAlignment(Qt::AlignCenter);
    auto firstStartWidget = gsl::owner<FirstStartWidget*>(new FirstStartWidget(firstStartScrollWidget));
    connect(firstStartWidget, &FirstStartWidget::dismissed, this, &StartView::firstStartWidgetDismissed);
    connect(
        firstStartWidget,
        &FirstStartWidget::configureAIRequested,
        this,
        &StartView::openVibeCADPreferences
    );
    connect(
        firstStartWidget,
        &FirstStartWidget::openAssistantRequested,
        this,
        &StartView::openVibeCADAssistant
    );
    firstStartRegion->addWidget(firstStartWidget);
    _contents->addWidget(firstStartScrollArea);

    // Documents page
    auto documentsWidget = gsl::owner<QWidget*>(new QWidget());
    documentsWidget->setObjectName(QLatin1String("VibeCADStartDocuments"));
    _contents->addWidget(documentsWidget);
    auto documentsMainLayout = gsl::owner<QVBoxLayout*>(new QVBoxLayout());
    documentsMainLayout->setContentsMargins(0, 0, 0, 0);
    documentsWidget->setLayout(documentsMainLayout);
    auto documentsScrollArea = gsl::owner<QScrollArea*>(new QScrollArea());
    documentsScrollArea->setVerticalScrollBarPolicy(Qt::ScrollBarPolicy::ScrollBarAsNeeded);
    documentsMainLayout->addWidget(documentsScrollArea);
    auto documentsScrollWidget = gsl::owner<QWidget*>(new QWidget(documentsScrollArea));
    documentsScrollArea->setWidget(documentsScrollWidget);
    documentsScrollArea->setWidgetResizable(true);

    auto documentsViewportLayout = gsl::owner<QHBoxLayout*>(new QHBoxLayout(documentsScrollWidget));
    documentsViewportLayout->setContentsMargins(28, 24, 28, 24);
    auto documentsContentWidget = gsl::owner<QWidget*>(new QWidget(documentsScrollWidget));
    documentsContentWidget->setObjectName(QLatin1String("VibeCADStartContent"));
    documentsContentWidget->setMaximumWidth(1440);
    documentsContentWidget->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Preferred);
    documentsViewportLayout->addStretch();
    documentsViewportLayout->addWidget(documentsContentWidget, 1);
    documentsViewportLayout->addStretch();

    auto documentsContentLayout = gsl::owner<QVBoxLayout*>(new QVBoxLayout(documentsContentWidget));
    documentsContentLayout->setContentsMargins(0, 0, 0, 0);
    documentsContentLayout->setSizeConstraint(QLayout::SizeConstraint::SetMinAndMaxSize);

    auto hero = gsl::owner<QFrame*>(new QFrame(documentsContentWidget));
    hero->setObjectName(QLatin1String("VibeCADStartHero"));
    auto heroLayout = gsl::owner<QVBoxLayout*>(new QVBoxLayout(hero));
    heroLayout->setContentsMargins(24, 22, 24, 22);
    heroLayout->setSpacing(18);

    auto heroTopLayout = gsl::owner<QHBoxLayout*>(new QHBoxLayout);
    heroTopLayout->setSpacing(18);

    auto heroMark = gsl::owner<QLabel*>(new QLabel(hero));
    heroMark->setObjectName(QLatin1String("VibeCADStartHeroMark"));
    heroMark->setPixmap(QIcon(QLatin1String(":/icons/vibecad.svg")).pixmap(68, 68));
    heroMark->setFixedSize(68, 68);
    heroTopLayout->addWidget(heroMark, 0, Qt::AlignTop);

    auto heroTextLayout = gsl::owner<QVBoxLayout*>(new QVBoxLayout);
    heroTextLayout->setSpacing(4);
    _heroTitleLabel = gsl::owner<QLabel*>(new QLabel(hero));
    _heroTitleLabel->setObjectName(QLatin1String("VibeCADStartBrandTitle"));
    heroTextLayout->addWidget(_heroTitleLabel);
    _heroDescriptionLabel = gsl::owner<QLabel*>(new QLabel(hero));
    _heroDescriptionLabel->setObjectName(QLatin1String("VibeCADStartBrandDescription"));
    _heroDescriptionLabel->setWordWrap(true);
    heroTextLayout->addWidget(_heroDescriptionLabel);
    heroTopLayout->addLayout(heroTextLayout, 1);
    heroLayout->addLayout(heroTopLayout);

    auto heroActions = gsl::owner<QHBoxLayout*>(new QHBoxLayout);
    heroActions->addStretch();
    _configureAI = gsl::owner<QPushButton*>(new QPushButton(hero));
    _configureAI->setObjectName(QLatin1String("VibeCADStartConfigureAI"));
    _configureAI->setIcon(QIcon(QLatin1String(":/icons/preferences-general.svg")));
    connect(_configureAI, &QPushButton::clicked, this, &StartView::openVibeCADPreferences);
    heroActions->addWidget(_configureAI);

    _openAssistant = gsl::owner<QPushButton*>(new QPushButton(hero));
    _openAssistant->setObjectName(QLatin1String("VibeCADStartOpenAssistant"));
    _openAssistant->setProperty("vibeStartPrimary", true);
    _openAssistant->setIcon(QIcon(QLatin1String(":/icons/vibecad.svg")));
    connect(_openAssistant, &QPushButton::clicked, this, &StartView::openVibeCADAssistant);
    heroActions->addWidget(_openAssistant);
    heroLayout->addLayout(heroActions);
    documentsContentLayout->addWidget(hero);

    _newFileLabel = gsl::owner<QLabel*>(new QLabel());
    _newFileLabel->setObjectName(QLatin1String("VibeCADStartSectionTitle"));
    documentsContentLayout->addWidget(_newFileLabel);

    auto createNewRow = gsl::owner<QWidget*>(new QWidget(documentsContentWidget));
    auto flowLayout = gsl::owner<FlowLayout*>(new FlowLayout);

    // Reset margins of layout to provide consistent spacing
    flowLayout->setContentsMargins({});

    // This allows new file widgets to be targeted via QSS
    createNewRow->setObjectName(QStringLiteral("CreateNewRow"));
    createNewRow->setLayout(flowLayout);

    documentsContentLayout->addWidget(createNewRow);
    configureNewFileButtons(flowLayout);

    _recentFilesLabel = gsl::owner<QLabel*>(new QLabel());
    _recentFilesLabel->setObjectName(QLatin1String("VibeCADStartSectionTitle"));
    documentsContentLayout->addWidget(_recentFilesLabel);
    auto recentFilesListWidget = gsl::owner<FileCardView*>(new FileCardView(documentsContentWidget));
    recentFilesListWidget->setObjectName(QLatin1String("RecentFilesList"));
    connect(recentFilesListWidget, &QListView::clicked, this, &StartView::fileCardSelected);
    documentsContentLayout->addWidget(recentFilesListWidget);

    FileCardView* customFolderListWidget {};
    if (showCustomFolder) {
        customFolderListWidget = gsl::owner<FileCardView*>(new FileCardView(documentsContentWidget));
        customFolderListWidget->setObjectName(QLatin1String("CustomFolderList"));
        _customFolderLabel = gsl::owner<QLabel*>(new QLabel());
        _customFolderLabel->setObjectName(QLatin1String("VibeCADStartSectionTitle"));
        documentsContentLayout->addWidget(_customFolderLabel);

        connect(customFolderListWidget, &QListView::clicked, this, &StartView::fileCardSelected);
        documentsContentLayout->addWidget(customFolderListWidget);
    }

    FileCardView* examplesListWidget {};
    if (showExamples) {
        examplesListWidget = gsl::owner<FileCardView*>(new FileCardView(documentsContentWidget));
        examplesListWidget->setObjectName(QLatin1String("ExamplesList"));
        _examplesLabel = gsl::owner<QLabel*>(new QLabel());
        _examplesLabel->setObjectName(QLatin1String("VibeCADStartSectionTitle"));
        documentsContentLayout->addWidget(_examplesLabel);

        connect(examplesListWidget, &QListView::clicked, this, &StartView::fileCardSelected);
        documentsContentLayout->addWidget(examplesListWidget);
    }

    documentsContentLayout->setSpacing(static_cast<int>(cardSpacing));
    documentsContentLayout->addStretch();


    // Documents page footer
    auto footerLayout = gsl::owner<QHBoxLayout*>(new QHBoxLayout());
    footerLayout->setContentsMargins(24, 8, 24, 10);
    documentsMainLayout->addLayout(footerLayout);

    _openFirstStart = gsl::owner<QPushButton*>(new QPushButton());
    _openFirstStart->setIcon(QIcon(QLatin1String(":/icons/preferences-general.svg")));
    connect(_openFirstStart, &QPushButton::clicked, this, &StartView::openFirstStartClicked);

    _showOnStartupCheckBox = gsl::owner<QCheckBox*>(new QCheckBox());
    bool showOnStartup = hGrp->GetBool("ShowOnStartup", true);
    _showOnStartupCheckBox->setCheckState(
        showOnStartup ? Qt::CheckState::Unchecked : Qt::CheckState::Checked
    );
    connect(_showOnStartupCheckBox, &QCheckBox::toggled, this, &StartView::showOnStartupChanged);

    footerLayout->addWidget(_openFirstStart);
    footerLayout->addStretch();
    footerLayout->addWidget(_showOnStartupCheckBox);

    setCentralWidget(_contents);

    // Set startup widget according to the first start parameter
    auto firstStart = hGrp->GetBool("FirstStart2024", true);
    _contents->setCurrentWidget(firstStart ? firstStartScrollArea : documentsWidget);
    if (customFolderListWidget) {
        configureCustomFolderListWidget(customFolderListWidget);
    }
    if (examplesListWidget) {
        configureExamplesListWidget(examplesListWidget);
    }
    configureRecentFilesListWidget(recentFilesListWidget, _recentFilesLabel);

    QTimer::singleShot(2000, this, [this, recentFilesListWidget]() {
        auto updateFun = [this, recentFilesListWidget]() {
            configureRecentFilesListWidget(recentFilesListWidget, _recentFilesLabel);
        };
        auto recentFiles = Gui::getMainWindow()->findChild<Gui::RecentFilesAction*>();
        if (recentFiles != nullptr) {
            connect(recentFiles, &Gui::RecentFilesAction::recentFilesListModified, this, updateFun);
        }
    });

    isInitialized = true;

    retranslateUi();
}

void StartView::configureNewFileButtons(QLayout* layout) const
{
    auto newEmptyFile = gsl::owner<NewFileButton*>(new NewFileButton(
        {tr("Empty File"),
         tr("Creates a new empty VibeCAD document"),
         QLatin1String(":/icons/document-new.svg")}
    ));
    newEmptyFile->setObjectName(QLatin1String("VibeCADNewFile"));
    auto openFile = gsl::owner<NewFileButton*>(new NewFileButton(
        {tr("Open File"),
         tr("Opens an existing CAD file or 3D model"),
         QLatin1String(":/icons/document-open.svg")}
    ));
    openFile->setObjectName(QLatin1String("VibeCADOpenFile"));
    auto partDesign = gsl::owner<NewFileButton*>(new NewFileButton(
        {tr("Parametric Body"),
         tr("Creates a body with the Part Design workbench"),
         QLatin1String(":/icons/PartDesignWorkbench.svg")}
    ));
    partDesign->setObjectName(QLatin1String("VibeCADParametricBody"));
    auto assembly = gsl::owner<NewFileButton*>(new NewFileButton(
        {tr("Assembly"),
         tr("Creates an assembly project"),
         QLatin1String(":/icons/AssemblyWorkbench.svg")}
    ));
    assembly->setObjectName(QLatin1String("VibeCADAssembly"));
    auto draft = gsl::owner<NewFileButton*>(new NewFileButton(
        {tr("2D Draft"), tr("Creates a 2D Draft document"), QLatin1String(":/icons/DraftWorkbench.svg")}
    ));
    draft->setObjectName(QLatin1String("VibeCADDraft"));
    // TODO: Ensure all of the required WBs are actually available
    layout->addWidget(partDesign);
    layout->addWidget(assembly);
    layout->addWidget(draft);
    layout->addWidget(newEmptyFile);
    layout->addWidget(openFile);

    connect(newEmptyFile, &QPushButton::clicked, this, &StartView::newEmptyFile);
    connect(openFile, &QPushButton::clicked, this, &StartView::openExistingFile);
    connect(partDesign, &QPushButton::clicked, this, &StartView::newPartDesignFile);
    connect(assembly, &QPushButton::clicked, this, &StartView::newAssemblyFile);
    connect(draft, &QPushButton::clicked, this, &StartView::newDraftFile);
}

void StartView::configureFileCardWidget(QListView* fileCardWidget)
{
    auto delegate = gsl::owner<FileCardDelegate*>(new FileCardDelegate(fileCardWidget));
    fileCardWidget->setItemDelegate(delegate);
    fileCardWidget->setMinimumWidth(0);
}


void StartView::configureRecentFilesListWidget(QListView* recentFilesListWidget, QLabel* recentFilesLabel)
{
    _recentFilesModel.loadRecentFiles();
    recentFilesListWidget->setModel(&_recentFilesModel);
    configureFileCardWidget(recentFilesListWidget);

    auto recentFilesGroup = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/RecentFiles"
    );
    auto numRecentFiles {recentFilesGroup->GetInt("RecentFiles", 0)};
    if (numRecentFiles == 0) {
        recentFilesListWidget->hide();
        recentFilesLabel->hide();
    }
    else {
        recentFilesListWidget->show();
        recentFilesLabel->show();
    }
}


void StartView::configureExamplesListWidget(QListView* examplesListWidget)
{
    _examplesModel.loadExamples();
    examplesListWidget->setModel(&_examplesModel);
    configureFileCardWidget(examplesListWidget);
}


void StartView::configureCustomFolderListWidget(QListView* customFolderListWidget)
{
    _customFolderModel.loadCustomFolder();
    customFolderListWidget->setModel(&_customFolderModel);
    configureFileCardWidget(customFolderListWidget);
}


void StartView::newEmptyFile()
{
    Gui::Application::Instance->commandManager().runCommandByName("Std_New");
    postStart(PostStartBehavior::switchWorkbench);
}

void StartView::newPartDesignFile()
{
    Gui::Application::Instance->commandManager().runCommandByName("Std_New");
    Gui::Application::Instance->activateWorkbench("PartDesignWorkbench");
    Gui::Application::Instance->commandManager().runCommandByName("PartDesign_Body");
    postStart(PostStartBehavior::doNotSwitchWorkbench);
}

void StartView::openExistingFile()
{
    auto originalDocument = Gui::Application::Instance->activeDocument();
    Gui::Application::Instance->commandManager().runCommandByName("Std_Open");
    Gui::Application::checkForRecomputes();
    if (Gui::Application::Instance->activeDocument() != originalDocument) {
        // Only run this if the user chose a new document to open (that is, they didn't cancel the
        // open file dialog)
        postStart(PostStartBehavior::switchWorkbench);
    }
}

void StartView::newAssemblyFile()
{
    Gui::Application::Instance->commandManager().runCommandByName("Std_New");
    Gui::Application::Instance->activateWorkbench("AssemblyWorkbench");
    Gui::Application::Instance->commandManager().runCommandByName("Assembly_CreateAssembly");
    Gui::Application::Instance->commandManager().runCommandByName("Std_Refresh");
    postStart(PostStartBehavior::doNotSwitchWorkbench);
}

void StartView::newDraftFile()
{
    Gui::Application::Instance->commandManager().runCommandByName("Std_New");
    Gui::Application::Instance->activateWorkbench("DraftWorkbench");
    Gui::Application::Instance->commandManager().runCommandByName("Std_ViewTop");
    postStart(PostStartBehavior::doNotSwitchWorkbench);
}

bool StartView::onHasMsg(const char* pMsg) const
{
    if (strcmp("AllowsOverlayOnHover", pMsg) == 0) {
        return false;
    }

    return MDIView::onHasMsg(pMsg);
}

void StartView::postStart(PostStartBehavior behavior)
{
    auto hGrp = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/Mod/Start"
    );

    if (behavior == PostStartBehavior::switchWorkbench) {
        auto wb = hGrp->GetASCII("AutoloadModule", "");
        if (wb == "$LastModule") {
            wb = App::GetApplication()
                     .GetParameterGroupByPath("User parameter:BaseApp/Preferences/General")
                     ->GetASCII("LastModule", "");
        }
        if (!wb.empty()) {
            Gui::Application::Instance->activateWorkbench(wb.c_str());
        }
    }
    if (hGrp->GetBool("closeStart", false)) {
        for (QWidget* w = this; w != nullptr; w = w->parentWidget()) {
            if (auto mdiSub = qobject_cast<QMdiSubWindow*>(w)) {
                mdiSub->close();
                return;
            }
        }
    }
}


void StartView::fileCardSelected(const QModelIndex& index)
{
    try {
        auto filename = index.data(static_cast<int>(Start::DisplayedFilesModelRoles::path)).toString();
        Gui::ModuleIO::verifyAndOpenFile(filename);
    }
    catch (Base::PyException& e) {
        Base::Console().error(e.getMessage().c_str());
    }
    catch (Base::Exception& e) {
        Base::Console().error(e.getMessage().c_str());
    }
    catch (...) {
        Base::Console().error("An unknown error occurred");
    }
}

void StartView::showOnStartupChanged(bool checked)
{
    auto hGrp = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/Mod/Start"
    );
    hGrp->SetBool(
        "ShowOnStartup",
        !checked
    );  // The sense of this option has been reversed: the checkbox actually says
        // "*Don't* show on startup" now, but the option is preserved in its
        // original sense, so is stored inverted.
}

void StartView::openFirstStartClicked()
{
    _contents->setCurrentIndex(0);
}

void StartView::firstStartWidgetDismissed()
{
    auto hGrp = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/Mod/Start"
    );
    hGrp->SetBool("FirstStart2024", false);
    _contents->setCurrentIndex(1);
}

void StartView::openVibeCADPreferences()
{
    try {
        // Preferences are an onboarding action, not a document command. Invoke the
        // dedicated entry point directly so global command-busy state cannot silently
        // discard the click.
        Base::Interpreter().runString(
            "import VibeCADGui; VibeCADGui.open_preferences(\"VibeCAD\")"
        );
        return;
    }
    catch (Base::PyException& error) {
        Base::Console().warning(
            "Could not open VibeCAD Preferences from the Start page: %s\n",
            error.getMessage().c_str()
        );
    }

    QMessageBox::warning(
        this,
        tr("VibeCAD setup unavailable"),
        tr("VibeCAD Preferences could not be opened. The VibeCAD module may not be available in "
           "this installation.")
    );
}

void StartView::openVibeCADAssistant()
{
    try {
        Base::Interpreter().runString("import VibeCADGui; VibeCADGui.open_assistant()");
        return;
    }
    catch (Base::PyException& error) {
        Base::Console().warning(
            "Could not open the VibeCAD Assistant from the Start page: %s\n",
            error.getMessage().c_str()
        );
    }

    QMessageBox::warning(
        this,
        tr("VibeCAD Assistant unavailable"),
        tr("The VibeCAD Assistant could not be opened. The VibeCAD module may not be available "
           "in this installation.")
    );
}

void StartView::changeEvent(QEvent* event)
{
    if (!isInitialized) {
        return;
    }

    _openFirstStart->setEnabled(true);
    Gui::Document* doc = Gui::Application::Instance->activeDocument();
    if (doc) {
        if (auto view = dynamic_cast<Gui::View3DInventor*>(doc->getActiveView())) {
            Gui::View3DInventorViewer* viewer = view->getViewer();
            if (viewer->isEditing()) {
                _openFirstStart->setEnabled(false);
            }
        }
    }

    if (event->type() == QEvent::LanguageChange) {
        this->retranslateUi();
    }

    Gui::MDIView::changeEvent(event);
}

void StartView::showEvent(QShowEvent* event)
{
    if (auto mainWindow = Gui::getMainWindow()) {
        if (auto mdiArea = mainWindow->findChild<QMdiArea*>()) {
            connect(
                mdiArea,
                &QMdiArea::subWindowActivated,
                this,
                &StartView::onMdiSubWindowActivated,
                Qt::UniqueConnection
            );
        }
    }
    Gui::MDIView::showEvent(event);
}

void StartView::onMdiSubWindowActivated(QMdiSubWindow* subWindow)
{
    // check if start view is activated subwindow if yes, then enable updates
    // so we can once again receive paint events
    bool isOurWindow = subWindow && subWindow->isAncestorOf(this);
    setListViewUpdatesEnabled(isOurWindow);
}

void StartView::setListViewUpdatesEnabled(bool enabled)
{
    // disable updates on all QListView widgets when inactive to prevent unnecessary paint events
    QList<QListView*> listViews = findChildren<QListView*>();
    for (QListView* listView : listViews) {
        listView->setUpdatesEnabled(enabled);
        if (listView->viewport()) {
            listView->viewport()->setUpdatesEnabled(enabled);
        }
    }
}

void StartView::recentFileAdded(const QString& filename)
{
    _recentFilesModel.recentFileAdded(filename);
}

void StartView::retranslateUi()
{
    QString title = QCoreApplication::translate("Workbench", "Start");
    setWindowTitle(title);

    const QLatin1String h1Start("<h1>");
    const QLatin1String h1End("</h1>");

    _heroTitleLabel->setText(tr("Welcome to VibeCAD"));
    _heroDescriptionLabel->setText(
        tr("Create, inspect, analyze, manufacture, and document real CAD with an AI collaborator "
           "at your side.")
    );
    _configureAI->setText(tr("AI Settings"));
    _openAssistant->setText(tr("Open Assistant"));

    _newFileLabel->setText(h1Start + tr("Start a design") + h1End);
    if (_examplesLabel) {
        _examplesLabel->setText(h1Start + tr("Explore examples") + h1End);
    }
    _recentFilesLabel->setText(h1Start + tr("Continue working") + h1End);

    auto hGrp = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/Mod/Start"
    );
    std::string customFolder(hGrp->GetASCII("CustomFolder", ""));
    if (!customFolder.empty() && _customFolderLabel) {
        if (hGrp->GetBool("ShortCustomFolder", true)) {
            _customFolderLabel->setToolTip(QString::fromUtf8(customFolder.c_str()));
            customFolder = customFolder.substr(customFolder.find_last_of("/\\") + 1);
        }
        _customFolderLabel->setText(h1Start + QString::fromUtf8(customFolder.c_str()) + h1End);
    }

    _openFirstStart->setText(tr("Setup & appearance"));
    _showOnStartupCheckBox->setText(tr("Do not show this Start page again (start with blank screen)"));
}
