// SPDX-License-Identifier: LGPL-2.1-or-later

#include <QDataStream>
#include <QDockWidget>
#include <QMainWindow>
#include <QSet>
#include <QTest>

#include "Gui/DockWindowManager.h"

namespace
{

constexpr auto assistantName = "VibeCADAssistantPanel";
constexpr auto modelCodeName = "VibeCADScriptedModelPanel";
constexpr auto taskName = "Std_TaskView";

QDockWidget* addDock(QMainWindow& window, const char* name, Qt::DockWidgetArea area)
{
    auto* dock = new QDockWidget(&window);
    dock->setObjectName(QString::fromUtf8(name));
    window.addDockWidget(area, dock);
    return dock;
}

QSet<QString> tabNames(const QMainWindow& window, QDockWidget* dock)
{
    QSet<QString> result;
    for (const auto* peer : window.tabifiedDockWidgets(dock)) {
        result.insert(peer->objectName());
    }
    return result;
}

int serializedStringOccurrences(const QByteArray& state, const QString& value)
{
    QByteArray token;
    QDataStream stream(&token, QIODevice::WriteOnly);
    stream.setVersion(QDataStream::Qt_5_0);
    stream << value;
    return static_cast<int>(state.count(token));
}

}  // namespace

class DockLayoutStateTest: public QObject
{
    Q_OBJECT

private Q_SLOTS:
    void lateDockConsumesSavedPlaceholder()
    {
        QMainWindow source;
        auto* sourceTask = addDock(source, taskName, Qt::RightDockWidgetArea);
        auto* sourceAssistant = addDock(source, assistantName, Qt::RightDockWidgetArea);
        source.tabifyDockWidget(sourceTask, sourceAssistant);
        const QByteArray state = source.saveState();

        QMainWindow restored;
        auto* restoredTask = addDock(restored, taskName, Qt::LeftDockWidgetArea);
        QVERIFY(restored.restoreState(state));

        auto* restoredAssistant = new QDockWidget(&restored);
        restoredAssistant->setObjectName(QString::fromUtf8(assistantName));
        QVERIFY(restored.restoreDockWidget(restoredAssistant));
        QCOMPARE(restored.dockWidgetArea(restoredAssistant), Qt::RightDockWidgetArea);
        QCOMPARE(restored.dockWidgetArea(restoredTask), Qt::RightDockWidgetArea);
        QCOMPARE(serializedStringOccurrences(restored.saveState(), QString::fromUtf8(assistantName)), 1);
    }

    void duplicateDockRecordsAreCollapsedToSavedTabGroup()
    {
        QMainWindow corruptSource;
        addDock(corruptSource, assistantName, Qt::LeftDockWidgetArea);
        addDock(corruptSource, modelCodeName, Qt::LeftDockWidgetArea);
        auto* sourceTask = addDock(corruptSource, taskName, Qt::RightDockWidgetArea);
        auto* groupedAssistant = addDock(corruptSource, assistantName, Qt::RightDockWidgetArea);
        auto* groupedModelCode = addDock(corruptSource, modelCodeName, Qt::RightDockWidgetArea);
        corruptSource.tabifyDockWidget(sourceTask, groupedAssistant);
        corruptSource.tabifyDockWidget(sourceTask, groupedModelCode);
        const QByteArray corruptState = corruptSource.saveState();

        QCOMPARE(serializedStringOccurrences(corruptState, QString::fromUtf8(assistantName)), 2);
        QCOMPARE(serializedStringOccurrences(corruptState, QString::fromUtf8(modelCodeName)), 2);

        QMainWindow restored;
        auto* restoredAssistant = addDock(restored, assistantName, Qt::LeftDockWidgetArea);
        auto* restoredModelCode = addDock(restored, modelCodeName, Qt::LeftDockWidgetArea);
        auto* restoredTask = addDock(restored, taskName, Qt::LeftDockWidgetArea);
        QVERIFY(restored.restoreState(corruptState));
        QVERIFY(Gui::DockWindowManager::repairDuplicateDockState(&restored, corruptState));

        QCOMPARE(restored.dockWidgetArea(restoredAssistant), Qt::RightDockWidgetArea);
        QCOMPARE(restored.dockWidgetArea(restoredModelCode), Qt::RightDockWidgetArea);
        QCOMPARE(restored.dockWidgetArea(restoredTask), Qt::RightDockWidgetArea);
        QCOMPARE(
            tabNames(restored, restoredAssistant),
            QSet<QString>({QString::fromUtf8(modelCodeName), QString::fromUtf8(taskName)})
        );
        QCOMPARE(
            tabNames(restored, restoredModelCode),
            QSet<QString>({QString::fromUtf8(assistantName), QString::fromUtf8(taskName)})
        );
        QCOMPARE(
            tabNames(restored, restoredTask),
            QSet<QString>({QString::fromUtf8(assistantName), QString::fromUtf8(modelCodeName)})
        );

        const QByteArray repairedState = restored.saveState();
        QCOMPARE(serializedStringOccurrences(repairedState, QString::fromUtf8(assistantName)), 1);
        QCOMPARE(serializedStringOccurrences(repairedState, QString::fromUtf8(modelCodeName)), 1);
        QCOMPARE(serializedStringOccurrences(repairedState, QString::fromUtf8(taskName)), 1);
        for (const auto* dock : restored.findChildren<QDockWidget*>()) {
            QVERIFY(!dock->objectName().startsWith("__FreeCADDockStateRecovery_"));
        }

        QMainWindow secondRestart;
        auto* secondAssistant = addDock(secondRestart, assistantName, Qt::LeftDockWidgetArea);
        auto* secondModelCode = addDock(secondRestart, modelCodeName, Qt::LeftDockWidgetArea);
        auto* secondTask = addDock(secondRestart, taskName, Qt::LeftDockWidgetArea);
        QVERIFY(secondRestart.restoreState(repairedState));
        QVERIFY(!Gui::DockWindowManager::repairDuplicateDockState(&secondRestart, repairedState));
        QCOMPARE(
            tabNames(secondRestart, secondAssistant),
            QSet<QString>({QString::fromUtf8(modelCodeName), QString::fromUtf8(taskName)})
        );
        QCOMPARE(
            tabNames(secondRestart, secondModelCode),
            QSet<QString>({QString::fromUtf8(assistantName), QString::fromUtf8(taskName)})
        );
        QCOMPARE(
            tabNames(secondRestart, secondTask),
            QSet<QString>({QString::fromUtf8(assistantName), QString::fromUtf8(modelCodeName)})
        );
    }
};

QTEST_MAIN(DockLayoutStateTest)

#include "DockLayoutState.moc"
