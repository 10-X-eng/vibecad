# SPDX-License-Identifier: LGPL-2.1-or-later

"""Contracts for the Windows visible-operator tour entry point."""

from __future__ import annotations

import ast
from collections import Counter
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
OPERATOR_SCRIPT = REPOSITORY_ROOT / "Invoke-VibeCAD-VisibleTour.ps1"
AGENT_CONTROL = (
    REPOSITORY_ROOT / "src" / "Mod" / "VibeCAD" / "VibeCADAgentControl.py"
)
PHYSICAL_INPUT_IMPLEMENTATIONS = (OPERATOR_SCRIPT, AGENT_CONTROL)
PROHIBITED_PHYSICAL_INPUT_APIS = (
    "SetCursorPos",
    "SendInput",
    "mouse_event",
    "SetCursor",
    "SetSystemCursor",
    "keybd_event",
    "SendKeys",
    "SendWait",
    "SetKeyboardState",
    "GetAsyncKeyState",
    "BlockInput",
    "ClipCursor",
    "SetCapture",
    "ReleaseCapture",
    "AttachThreadInput",
    "PostMessage",
    "PostMessageA",
    "PostMessageW",
    "PostThreadMessage",
    "SendMessage",
    "SendMessageA",
    "SendMessageW",
    "SendNotifyMessageA",
    "SendNotifyMessageW",
    "SetForegroundWindow",
    "SetFocus",
    "SetActiveWindow",
    "BringWindowToTop",
    "SwitchToThisWindow",
    "SetWindowsHookEx",
    "SetWindowsHookExA",
    "SetWindowsHookExW",
    "RegisterHotKey",
    "RegisterRawInputDevices",
    "pyautogui",
    "pynput",
    "robotjs",
)
PROHIBITED_PYTHON_INPUT_MODULES = {
    "keyboard",
    "mouse",
    "pyautogui",
    "pynput",
    "win32api",
    "win32con",
    "win32gui",
    "win32process",
}
ALLOWED_CTYPES_WINDOWS_LIBRARIES = {"advapi32", "kernel32"}
PROHIBITED_QT_INPUT_CALLS = {
    "QtGui.QCursor.setPos",
    "QtTest.QTest.keyClick",
    "QtTest.QTest.keyClicks",
    "QtTest.QTest.keyPress",
    "QtTest.QTest.keyRelease",
    "QtTest.QTest.mouseMove",
    "QtTest.QTest.mousePress",
    "QtTest.QTest.mouseRelease",
    "QtTest.QTest.mouseDClick",
    "QCoreApplication.postEvent",
    "QCoreApplication.sendEvent",
    "QApplication.postEvent",
    "QApplication.sendEvent",
    "QtCore.QCoreApplication.postEvent",
    "QtCore.QCoreApplication.sendEvent",
    "QtWidgets.QApplication.postEvent",
    "QtWidgets.QApplication.sendEvent",
}
ALLOWED_USER32_IMPORTS = (
    ("user32.dll", "GetWindowRect"),
    ("user32.dll", "GetWindowThreadProcessId"),
    ("user32.dll", "IsIconic"),
    ("user32.dll", "ShowWindow"),
    ("user32.dll", "SetWindowPos"),
)
TOP_LEVEL_RECEIPT_KEYS = (
    "schema",
    "ok",
    "channel",
    "custom_cursor",
    "virtual_cursor_color",
    "physical_cursor_control",
    "input_method",
    "exact_process_id",
    "executable",
    "target_count",
    "targets",
    "completed_at_utc",
    "receipt_path",
)
TARGET_RECEIPT_KEYS = (
    "sequence",
    "target_kind",
    "requested_text",
    "semantic_index",
    "screen_x",
    "screen_y",
    "selected_text",
    "menu_visible",
    "exact_process_id",
    "input_method",
    "physical_cursor_control",
    "physical_cursor_unchanged_during_click",
    "virtual_cursor_color",
    "semantic_verified",
    "verified_at_utc",
)


def _script() -> str:
    return OPERATOR_SCRIPT.read_text(encoding="utf-8")


def _dotted_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _embedded_operator_source(source: str) -> str:
    match = re.search(
        r'^\s*\$OperatorSource\s*=\s*@"\r?\n(?P<csharp>.*?)\r?\n"@\s*$',
        source,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    return match.group("csharp")


def _powershell() -> str:
    # The one-click Windows entry points use Windows PowerShell. Prefer that
    # runtime when it is available, with cross-platform pwsh as the fallback.
    executable = shutil.which("powershell") or shutil.which("pwsh")
    if executable is None:
        pytest.skip("PowerShell is required for the visible-tour receipt contract")
    return executable


def _powershell_literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _function_loader(function_names: tuple[str, ...]) -> str:
    names = ", ".join(_powershell_literal(name) for name in function_names)
    return f"""
$Tokens = $null
$ParseErrors = $null
$SourceAst = [System.Management.Automation.Language.Parser]::ParseFile(
    {_powershell_literal(OPERATOR_SCRIPT)},
    [ref]$Tokens,
    [ref]$ParseErrors
)
if ($ParseErrors.Count -ne 0) {{
    throw ($ParseErrors | Out-String)
}}
foreach ($FunctionName in @({names})) {{
    $FunctionAst = $SourceAst.FindAll({{
        param($Node)
        $Node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $Node.Name -eq $FunctionName
    }}, $true) | Select-Object -First 1
    if ($null -eq $FunctionAst) {{
        throw "Missing receipt function $FunctionName."
    }}
    Invoke-Expression $FunctionAst.Extent.Text
}}
"""


def _receipt_fixture_script(receipt_path: Path) -> str:
    return (
        "$ErrorActionPreference = 'Stop'\n"
        + _function_loader(
            ("New-VibeCADTourReceiptPayload", "Write-VibeCADTourReceipt")
        )
        + f"""
$ReceiptPath = {_powershell_literal(receipt_path)}
$Targets = @(
    [pscustomobject][ordered]@{{
        sequence = 1
        target_kind = 'ribbon'
        requested_text = 'Aero'
        semantic_index = 3
        screen_x = 410
        screen_y = 77
        selected_text = 'Aero'
        menu_visible = $null
        exact_process_id = 4321
        input_method = 'qt_in_process_mouse_click'
        physical_cursor_control = 'none'
        physical_cursor_unchanged_during_click = $false
        virtual_cursor_color = 'cyan'
        semantic_verified = $true
        verified_at_utc = '2026-08-29T17:45:00.0000000Z'
    }},
    [pscustomobject][ordered]@{{
        sequence = 2
        target_kind = 'menu'
        requested_text = 'File'
        semantic_index = 0
        screen_x = 35
        screen_y = 14
        selected_text = $null
        menu_visible = $true
        exact_process_id = 4321
        input_method = 'qt_in_process_menu_popup'
        physical_cursor_control = 'none'
        physical_cursor_unchanged_during_click = $true
        virtual_cursor_color = 'cyan'
        semantic_verified = $true
        verified_at_utc = '2026-08-29T17:45:01.0000000Z'
    }}
)
$Payload = New-VibeCADTourReceiptPayload `
    -ProcessId 4321 `
    -Executable 'C:\\VibeCAD\\package\\rattler-build\\.pixi\\envs\\default\\Library\\bin\\VibeCAD.exe' `
    -Targets $Targets `
    -ReceiptPath $ReceiptPath `
    -CompletedAtUtc '2026-08-29T17:45:02.0000000Z'
$Json = Write-VibeCADTourReceipt -Payload $Payload -ReceiptPath $ReceiptPath
[Console]::Out.Write($Json)
"""
    )


def _run_powershell(script_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            _powershell(),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_visible_tour_is_a_repo_local_one_click_entry_point() -> None:
    source = _script()
    assert "/v1/ui/ribbon" in source
    assert "/v1/ui/menus" in source
    assert ".vibecad-dev\\agent" in source
    assert "127.0.0.1" in source
    assert "package\\rattler-build\\.pixi\\envs\\default" in source
    assert "VibeCAD.exe" in source
    assert "freecad.exe" in source.lower()
    assert "$ExecutableCandidates" in source


def test_visible_tour_uses_semantic_targets_and_exact_window_safety() -> None:
    source = _script()
    lowered = source.lower()
    assert "GetWindowRect" in source
    assert "screen_rect.center_x" in lowered
    assert "screen_rect.center_y" in lowered
    assert "/v1/ui/click" in source
    assert "physical_cursor_control" in lowered


def test_tester_implementations_never_take_over_physical_mouse_or_keyboard() -> None:
    violations: list[str] = []
    for implementation in PHYSICAL_INPUT_IMPLEMENTATIONS:
        source = implementation.read_text(encoding="utf-8")
        for prohibited_api in PROHIBITED_PHYSICAL_INPUT_APIS:
            if re.search(rf"\b{re.escape(prohibited_api)}\b", source, re.IGNORECASE):
                violations.append(f"{implementation.name}: {prohibited_api}")

    assert violations == []
    agent_source = AGENT_CONTROL.read_text(encoding="utf-8")
    agent_tree = ast.parse(agent_source, filename=str(AGENT_CONTROL))
    imported_modules: set[str] = set()
    calls: Counter[str] = Counter()
    ctypes_windows_libraries: list[str | None] = []
    for node in ast.walk(agent_tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module.split(".", 1)[0])
        elif isinstance(node, ast.Call):
            call_name = _dotted_name(node.func)
            calls[call_name] += 1
            if call_name == "ctypes.WinDLL":
                library = None
                if (
                    node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    library = node.args[0].value.lower()
                ctypes_windows_libraries.append(library)

    assert imported_modules.isdisjoint(PROHIBITED_PYTHON_INPUT_MODULES)
    assert "ctypes.windll" not in agent_source
    assert None not in ctypes_windows_libraries
    assert set(ctypes_windows_libraries) <= ALLOWED_CTYPES_WINDOWS_LIBRARIES
    assert not (set(calls) & PROHIBITED_QT_INPUT_CALLS)
    assert calls["QtGui.QCursor.pos"] == 1
    assert calls["QtTest.QTest.mouseClick"] == 1
    assert calls["target_menu.popup"] == 1

    operator_source = _script()
    assert not re.search(
        r"New-Object\s+-ComObject\s+WScript\.Shell|"
        r"\.CreateObject\(\s*['\"]WScript\.Shell['\"]\s*\)|"
        r"\.SendKeys\s*\(|"
        r"\bInvoke-Expression\b|"
        r"\biex\b|"
        r"Start-Process[^\r\n]*(?:AutoHotkey|nircmd|xdotool)",
        operator_source,
        re.IGNORECASE,
    )


def test_visible_tour_native_calls_are_exactly_allowlisted_and_nonactivating() -> None:
    source = _script()
    csharp = _embedded_operator_source(source)
    native_imports = tuple(
        (library.lower(), function)
        for library, function in re.findall(
            r'\[DllImport\("([^"]+)"\)\]\s*'
            r"public\s+static\s+extern\s+\w+\s+(\w+)\s*\(",
            csharp,
            re.DOTALL,
        )
    )
    assert native_imports == ALLOWED_USER32_IMPORTS

    assert csharp.count("NativeWindow.SetWindowPos(") == 1
    assert re.search(
        r"NativeWindow\.SetWindowPos\(\s*"
        r"Handle,\s*topMost,\s*0,\s*0,\s*0,\s*0,\s*"
        r"SWP_NOSIZE\s*\|\s*SWP_NOMOVE\s*\|\s*"
        r"SWP_NOACTIVATE\s*\|\s*SWP_SHOWWINDOW\s*\);",
        csharp,
        re.DOTALL,
    )

    restore_start = source.index("function Restore-VibeCADWindowIfMinimized")
    restore_end = source.index("function Assert-TargetInsideExactWindow", restore_start)
    restore_body = source[restore_start:restore_end]
    assert restore_body.count("::ShowWindow(") == 1
    assert restore_body.index("Get-VibeCADWindowBounds") < restore_body.index(
        "::ShowWindow("
    )
    assert "SW_SHOWNOACTIVATE" in restore_body
    assert "SW_RESTORE" not in source


def test_visible_tour_has_a_plain_cyan_virtual_cursor() -> None:
    source = _script()
    assert "VibeCADVirtualCursor" in source
    assert "WS_EX_TRANSPARENT" in source
    assert "WS_EX_NOACTIVATE" in source
    assert "Color.Cyan" in source
    assert "Color.FromArgb" not in source
    assert "AGENT" not in source
    assert "DrawEllipse" not in source
    assert "ShowWithoutActivation" in source


def test_visible_tour_discovers_live_menus_and_tabs_without_feature_assumptions() -> None:
    source = _script()
    assert "function Get-VibeCADDefaultTourTargets" in source
    assert "$Targets = @(Get-VibeCADDefaultTourTargets)" in source
    assert '$Targets = @()' in source
    assert '"menu:$([string]$Menu.text)"' in source
    assert '"ribbon:$([string]$Tab.text)"' in source
    assert source.count('$DefaultTargets.Add("') == 2
    assert "VerificationTimeoutSeconds" in source
    assert "$VerificationTimeoutSeconds = 30" in source
    assert "selected_text" in source
    assert "menu_visible" in source
    assert "menu_open_after" in source
    assert "focus_restored" in source
    assert "active_window_unchanged" in source
    assert "popup_restored" in source
    assert "active_action_restored" in source
    assert "interaction_restored" in source
    assert "preview_duration_milliseconds" in source
    assert "Start-Sleep" in source
    assert "throw" in source


def test_visible_tour_still_accepts_an_explicit_focused_sequence() -> None:
    source = _script()
    assert "foreach ($RequestedTarget in @($Targets))" in source
    assert "Resolve-VibeCADUiTarget" in source


def test_visible_tour_restores_only_the_exact_minimized_vibecad_window() -> None:
    source = _script()

    assert "IsIconic" in source
    assert "ShowWindow" in source
    assert "SW_SHOWNOACTIVATE" in source
    assert "GetWindowThreadProcessId" in source


def test_visible_tour_default_receipt_paths_are_unique_and_collision_resistant(
    tmp_path: Path,
) -> None:
    helper_path = tmp_path / "default-receipt-paths.ps1"
    helper_path.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        + _function_loader(("New-VibeCADTourReceiptPath",))
        + f"""
$RepositoryRoot = {_powershell_literal(tmp_path)}
$First = New-VibeCADTourReceiptPath -RepositoryRoot $RepositoryRoot
$Second = New-VibeCADTourReceiptPath -RepositoryRoot $RepositoryRoot
[Console]::Out.Write(([ordered]@{{ first = $First; second = $Second }} | ConvertTo-Json -Compress))
""",
        encoding="utf-8",
    )

    result = _run_powershell(helper_path)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["first"] != payload["second"]
    expected_parent = (tmp_path / ".vibecad-dev" / "tours").resolve()
    for receipt_path in payload.values():
        path = Path(receipt_path)
        assert path.parent.resolve() == expected_parent
        assert re.fullmatch(
            r"visible-tour-\d{8}T\d{13}Z-[0-9a-f]{32}\.json",
            path.name,
        )


def test_visible_tour_receipt_schema_order_and_persisted_output_are_identical(
    tmp_path: Path,
) -> None:
    receipt_path = (tmp_path / "evidence" / "fixed-receipt.json").resolve()
    helper_path = tmp_path / "write-receipt.ps1"
    helper_path.write_text(_receipt_fixture_script(receipt_path), encoding="utf-8")

    result = _run_powershell(helper_path)

    assert result.returncode == 0, result.stderr
    persisted = receipt_path.read_text(encoding="utf-8")
    assert persisted == result.stdout
    payload = json.loads(persisted)
    assert tuple(payload) == TOP_LEVEL_RECEIPT_KEYS
    assert payload["schema"] == "vibecad.visible-operator-receipt.v1"
    assert payload["ok"] is True
    assert payload["channel"] == "vibecad-visible-operator"
    assert payload["custom_cursor"] == "VibeCADVirtualCursor"
    assert payload["virtual_cursor_color"] == "cyan"
    assert payload["physical_cursor_control"] == "none"
    assert payload["input_method"] == "qt_in_process_semantic_activation"
    assert payload["exact_process_id"] == 4321
    assert payload["executable"] == (
        "C:\\VibeCAD\\package\\rattler-build\\.pixi\\envs\\default\\"
        "Library\\bin\\VibeCAD.exe"
    )
    assert payload["target_count"] == len(payload["targets"]) == 2
    assert payload["completed_at_utc"] == "2026-08-29T17:45:02.0000000Z"
    assert Path(payload["receipt_path"]).resolve() == receipt_path

    assert [target["sequence"] for target in payload["targets"]] == [1, 2]
    assert [target["target_kind"] for target in payload["targets"]] == [
        "ribbon",
        "menu",
    ]
    assert [target["requested_text"] for target in payload["targets"]] == [
        "Aero",
        "File",
    ]
    assert all(tuple(target) == TARGET_RECEIPT_KEYS for target in payload["targets"])
    assert all(target["exact_process_id"] == 4321 for target in payload["targets"])
    assert all(
        target["physical_cursor_control"] == "none"
        for target in payload["targets"]
    )
    assert all(target["semantic_verified"] is True for target in payload["targets"])
    assert payload["targets"][0]["selected_text"] == "Aero"
    assert payload["targets"][0]["menu_visible"] is None
    assert payload["targets"][0]["semantic_index"] == 3
    assert payload["targets"][0]["screen_x"] == 410
    assert payload["targets"][0]["screen_y"] == 77
    assert payload["targets"][0]["input_method"] == "qt_in_process_mouse_click"
    assert payload["targets"][0]["physical_cursor_unchanged_during_click"] is False
    assert payload["targets"][0]["virtual_cursor_color"] == "cyan"
    assert (
        payload["targets"][0]["verified_at_utc"]
        == "2026-08-29T17:45:00.0000000Z"
    )
    assert payload["targets"][1]["selected_text"] is None
    assert payload["targets"][1]["menu_visible"] is True
    assert payload["targets"][1]["semantic_index"] == 0
    assert payload["targets"][1]["screen_x"] == 35
    assert payload["targets"][1]["screen_y"] == 14
    assert payload["targets"][1]["input_method"] == "qt_in_process_menu_popup"
    assert payload["targets"][1]["physical_cursor_unchanged_during_click"] is True
    assert payload["targets"][1]["virtual_cursor_color"] == "cyan"
    assert (
        payload["targets"][1]["verified_at_utc"]
        == "2026-08-29T17:45:01.0000000Z"
    )


def test_visible_tour_receipt_refuses_to_overwrite_existing_evidence(
    tmp_path: Path,
) -> None:
    receipt_path = (tmp_path / "evidence" / "fixed-receipt.json").resolve()
    helper_path = tmp_path / "write-receipt.ps1"
    helper_path.write_text(_receipt_fixture_script(receipt_path), encoding="utf-8")
    first = _run_powershell(helper_path)
    assert first.returncode == 0, first.stderr
    original = receipt_path.read_bytes()

    collision = _run_powershell(helper_path)

    assert collision.returncode != 0
    assert "refusing to overwrite" in (collision.stdout + collision.stderr).lower()
    assert receipt_path.read_bytes() == original
