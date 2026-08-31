# SPDX-License-Identifier: LGPL-2.1-or-later

<#
.SYNOPSIS
Runs a visible, semantically verified VibeCAD UI tour with a virtual cursor.

.DESCRIPTION
The tour draws its own plain cyan cursor over VibeCAD. It never moves or
clicks the user's physical Windows cursor. Geometry comes from live Qt
semantics, while each click is injected directly into the named Qt ribbon or
menu target through this checkout's authenticated loopback agent channel.

The command validates the exact checkout process, window bounds, semantic
target, injected-click response, and post-click UI state. Press Ctrl+C at any
time to stop the tour.
#>

[CmdletBinding()]
param(
    [Alias("Tabs")]
    [string[]] $Targets = @(),
    [ValidateRange(200, 10000)]
    [int] $MoveDurationMilliseconds = 1200,
    [ValidateRange(0, 10000)]
    [int] $DwellMilliseconds = 700,
    [ValidateRange(0, 10000)]
    [int] $AfterClickDwellMilliseconds = 900,
    [ValidateRange(1, 60)]
    [int] $VerificationTimeoutSeconds = 30,
    [ValidateRange(0, 2147483647)]
    [int] $VibeCADProcessId = 0,
    [string] $ReceiptPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function New-VibeCADTourReceiptPath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string] $RepositoryRoot
    )

    $FullRepositoryRoot = [IO.Path]::GetFullPath($RepositoryRoot)
    $TourDirectory = Join-Path $FullRepositoryRoot ".vibecad-dev\tours"
    [IO.Directory]::CreateDirectory($TourDirectory) | Out-Null
    $Timestamp = [DateTime]::UtcNow.ToString(
        "yyyyMMddTHHmmssfffffffZ",
        [Globalization.CultureInfo]::InvariantCulture
    )
    $Nonce = [Guid]::NewGuid().ToString("N")
    return [IO.Path]::GetFullPath(
        (Join-Path $TourDirectory "visible-tour-$Timestamp-$Nonce.json")
    )
}

function New-VibeCADTourReceiptPayload {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateRange(1, 2147483647)]
        [int] $ProcessId,
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string] $Executable,
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [object[]] $Targets,
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string] $ReceiptPath,
        [Parameter(Mandatory = $false)]
        [ValidateNotNullOrEmpty()]
        [string] $CompletedAtUtc = [DateTime]::UtcNow.ToString("o")
    )

    $TargetKeys = @(
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
        "verified_at_utc"
    )
    $NormalizedTargets = [Collections.Generic.List[object]]::new()

    for ($Index = 0; $Index -lt $Targets.Count; $Index += 1) {
        $Target = $Targets[$Index]
        if ($null -eq $Target) {
            throw "Tour receipt target $($Index + 1) is null."
        }
        $ActualTargetKeys = @($Target.PSObject.Properties.Name)
        if (
            $ActualTargetKeys.Count -ne $TargetKeys.Count -or
            @(Compare-Object $TargetKeys $ActualTargetKeys).Count -ne 0
        ) {
            throw "Tour receipt target $($Index + 1) does not match the required evidence schema."
        }

        $ExpectedSequence = $Index + 1
        if ([int]$Target.sequence -ne $ExpectedSequence) {
            throw "Tour receipt sequences must be contiguous and start at 1."
        }
        $Kind = [string]$Target.target_kind
        if ($Kind -notin @("ribbon", "menu")) {
            throw "Unsupported tour receipt target kind '$Kind'."
        }
        $RequestedText = [string]$Target.requested_text
        if ([string]::IsNullOrWhiteSpace($RequestedText)) {
            throw "Tour receipt target $ExpectedSequence has no requested text."
        }
        if ([int]$Target.semantic_index -lt 0) {
            throw "Tour receipt target $ExpectedSequence has an invalid semantic index."
        }
        if ([int]$Target.exact_process_id -ne $ProcessId) {
            throw "Tour receipt target $ExpectedSequence is not bound to PID $ProcessId."
        }
        if ([string]$Target.physical_cursor_control -ne "none") {
            throw "Tour receipt target $ExpectedSequence controlled the physical cursor."
        }
        if ([string]$Target.virtual_cursor_color -ne "cyan") {
            throw "Tour receipt target $ExpectedSequence did not use the plain cyan virtual cursor."
        }
        if ($Target.physical_cursor_unchanged_during_click -isnot [bool]) {
            throw "Tour receipt target $ExpectedSequence has invalid physical-cursor evidence."
        }
        if (
            $Target.semantic_verified -isnot [bool] -or
            -not [bool]$Target.semantic_verified
        ) {
            throw "Tour receipt target $ExpectedSequence was not semantically verified."
        }

        $InputMethod = [string]$Target.input_method
        $SelectedText = if ($null -eq $Target.selected_text) {
            $null
        }
        else {
            [string]$Target.selected_text
        }
        $MenuVisible = if ($null -eq $Target.menu_visible) {
            $null
        }
        else {
            if ($Target.menu_visible -isnot [bool]) {
                throw "Tour receipt target $ExpectedSequence has invalid menu visibility evidence."
            }
            [bool]$Target.menu_visible
        }
        if ($Kind -eq "ribbon") {
            if (
                $InputMethod -ne "qt_in_process_mouse_click" -or
                $SelectedText -ne $RequestedText -or
                $null -ne $MenuVisible
            ) {
                throw "Ribbon receipt target $ExpectedSequence has an invalid activation or postcondition."
            }
        }
        elseif (
            $InputMethod -ne "qt_in_process_menu_popup" -or
            $null -ne $SelectedText -or
            $MenuVisible -ne $true
        ) {
            throw "Menu receipt target $ExpectedSequence has an invalid activation or postcondition."
        }

        $ParsedVerifiedAt = [DateTimeOffset]::MinValue
        if (
            -not [DateTimeOffset]::TryParse(
                [string]$Target.verified_at_utc,
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::RoundtripKind,
                [ref]$ParsedVerifiedAt
            )
        ) {
            throw "Tour receipt target $ExpectedSequence has an invalid verification timestamp."
        }

        $NormalizedTargets.Add([pscustomobject][ordered]@{
            sequence = $ExpectedSequence
            target_kind = $Kind
            requested_text = $RequestedText
            semantic_index = [int]$Target.semantic_index
            screen_x = [int]$Target.screen_x
            screen_y = [int]$Target.screen_y
            selected_text = $SelectedText
            menu_visible = $MenuVisible
            exact_process_id = $ProcessId
            input_method = $InputMethod
            physical_cursor_control = "none"
            physical_cursor_unchanged_during_click = [bool]$Target.physical_cursor_unchanged_during_click
            virtual_cursor_color = "cyan"
            semantic_verified = $true
            verified_at_utc = [string]$Target.verified_at_utc
        })
    }

    $ParsedCompletedAt = [DateTimeOffset]::MinValue
    if (
        -not [DateTimeOffset]::TryParse(
            $CompletedAtUtc,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind,
            [ref]$ParsedCompletedAt
        )
    ) {
        throw "The tour receipt completion timestamp is invalid."
    }

    return [pscustomobject][ordered]@{
        schema = "vibecad.visible-operator-receipt.v1"
        ok = $true
        channel = "vibecad-visible-operator"
        custom_cursor = "VibeCADVirtualCursor"
        virtual_cursor_color = "cyan"
        physical_cursor_control = "none"
        input_method = "qt_in_process_semantic_activation"
        exact_process_id = $ProcessId
        executable = [IO.Path]::GetFullPath($Executable)
        target_count = $NormalizedTargets.Count
        targets = @($NormalizedTargets)
        completed_at_utc = $CompletedAtUtc
        receipt_path = [IO.Path]::GetFullPath($ReceiptPath)
    }
}

function Write-VibeCADTourReceipt {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNull()]
        [object] $Payload,
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string] $ReceiptPath
    )

    $TopLevelKeys = @(
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
        "receipt_path"
    )
    $ActualTopLevelKeys = @($Payload.PSObject.Properties.Name)
    if (
        $ActualTopLevelKeys.Count -ne $TopLevelKeys.Count -or
        ($ActualTopLevelKeys -join "`n") -cne ($TopLevelKeys -join "`n")
    ) {
        throw "The tour receipt payload does not match the required ordered schema."
    }

    $ResolvedReceiptPath = [IO.Path]::GetFullPath($ReceiptPath)
    if (
        -not ([string]$Payload.receipt_path).Equals(
            $ResolvedReceiptPath,
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "The tour receipt payload path does not match its write destination."
    }
    $ReceiptParent = Split-Path -Parent $ResolvedReceiptPath
    [IO.Directory]::CreateDirectory($ReceiptParent) | Out-Null

    $Json = $Payload | ConvertTo-Json -Depth 8
    $Utf8WithoutBom = [Text.UTF8Encoding]::new($false)
    $Bytes = $Utf8WithoutBom.GetBytes($Json)
    $Stream = $null
    try {
        $Stream = [IO.File]::Open(
            $ResolvedReceiptPath,
            [IO.FileMode]::CreateNew,
            [IO.FileAccess]::Write,
            [IO.FileShare]::None
        )
    }
    catch [IO.IOException] {
        if (Test-Path -LiteralPath $ResolvedReceiptPath) {
            throw "Refusing to overwrite existing VibeCAD tour receipt '$ResolvedReceiptPath'."
        }
        throw
    }

    try {
        $Stream.Write($Bytes, 0, $Bytes.Length)
        $Stream.Flush($true)
    }
    finally {
        if ($null -ne $Stream) {
            $Stream.Dispose()
        }
    }
    return $Json
}

if ($env:OS -ne "Windows_NT") {
    throw "Invoke-VibeCAD-VisibleTour.ps1 requires Windows for its virtual-cursor overlay."
}
$RepositoryRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$AgentHome = [IO.Path]::GetFullPath(
    (Join-Path $RepositoryRoot ".vibecad-dev\agent")
)
$AgentHomePrefix = $AgentHome.TrimEnd("\") + "\"
$EndpointPath = Join-Path $AgentHome "endpoint.json"
$EnvironmentBin = Join-Path $RepositoryRoot "package\rattler-build\.pixi\envs\default\Library\bin"
$ExecutableCandidates = @(
    (Join-Path $EnvironmentBin "VibeCAD.exe"),
    (Join-Path $EnvironmentBin "freecad.exe")
)
$ExpectedExecutable = $ExecutableCandidates |
    Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
    Select-Object -First 1
if (-not $ExpectedExecutable) {
    throw "This checkout has no built VibeCAD GUI executable. Run .\Launch-VibeCAD-Dev.ps1 first."
}
$ExpectedExecutable = [IO.Path]::GetFullPath([string]$ExpectedExecutable)

function Get-CheckoutAgentConnection {
    if (-not (Test-Path -LiteralPath $EndpointPath -PathType Leaf)) {
        throw "Missing checkout-scoped agent endpoint: $EndpointPath. Run .\Launch-VibeCAD-Dev.ps1 first."
    }
    $Endpoint = Get-Content -LiteralPath $EndpointPath -Raw | ConvertFrom-Json
    $BaseUri = [Uri][string]$Endpoint.base_url
    if ($BaseUri.Scheme -ne "http" -or $BaseUri.Host -notin @("127.0.0.1", "localhost", "::1")) {
        throw "Refusing non-loopback VibeCAD endpoint $BaseUri."
    }
    $TokenPath = [IO.Path]::GetFullPath([string]$Endpoint.token_path)
    if (-not $TokenPath.StartsWith($AgentHomePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing a bearer-token path outside this checkout's ignored agent home."
    }
    if (-not (Test-Path -LiteralPath $TokenPath -PathType Leaf)) {
        throw "The checkout-scoped bearer token is missing."
    }
    $Token = (Get-Content -LiteralPath $TokenPath -Raw).Trim()
    if ($Token.Length -lt 40) {
        throw "The checkout-scoped bearer token is invalid."
    }
    return [pscustomobject]@{
        BaseUrl = ([string]$Endpoint.base_url).TrimEnd("/")
        Headers = @{ Authorization = "Bearer $Token" }
    }
}

$Connection = Get-CheckoutAgentConnection

function Invoke-VibeCADAgentGet {
    param([Parameter(Mandatory = $true)][string] $Route)
    return Invoke-RestMethod `
        -Uri "$($Connection.BaseUrl)$Route" `
        -Headers $Connection.Headers `
        -Method Get `
        -TimeoutSec ([Math]::Max(5, $VerificationTimeoutSeconds))
}

function Invoke-VibeCADAgentPost {
    param(
        [Parameter(Mandatory = $true)][string] $Route,
        [Parameter(Mandatory = $true)][hashtable] $Body
    )
    return Invoke-RestMethod `
        -Uri "$($Connection.BaseUrl)$Route" `
        -Headers $Connection.Headers `
        -Method Post `
        -ContentType "application/json" `
        -Body ($Body | ConvertTo-Json -Depth 8 -Compress) `
        -TimeoutSec ([Math]::Max(5, $VerificationTimeoutSeconds))
}

$Status = Invoke-VibeCADAgentGet -Route "/v1/status"
if (
    -not $Status.ok -or
    $Status.channel -ne "vibecad-agent-control" -or
    -not $Status.gui_up
) {
    throw "The checkout-scoped VibeCAD GUI is not ready for virtual operation."
}

function Get-ExpectedVibeCADProcess {
    if ($VibeCADProcessId -gt 0) {
        $Candidates = @(Get-Process -Id $VibeCADProcessId -ErrorAction Stop)
    }
    else {
        $Candidates = @(
            Get-Process -Name "VibeCAD", "freecad" -ErrorAction SilentlyContinue |
                Where-Object {
                    try {
                        [IO.Path]::GetFullPath($_.Path).Equals(
                            $ExpectedExecutable,
                            [StringComparison]::OrdinalIgnoreCase
                        )
                    }
                    catch {
                        $false
                    }
                }
        )
    }
    if ($Candidates.Count -ne 1) {
        throw "Expected exactly one visible VibeCAD process from this checkout; found $($Candidates.Count)."
    }
    $Process = $Candidates[0]
    $Process.Refresh()
    if (-not $Process.Responding) {
        throw "VibeCAD PID $($Process.Id) is not responding."
    }
    $ActualExecutable = [IO.Path]::GetFullPath($Process.Path)
    if (-not $ActualExecutable.Equals($ExpectedExecutable, [StringComparison]::OrdinalIgnoreCase)) {
        throw "PID $($Process.Id) is not this checkout's VibeCAD executable."
    }
    return $Process
}

$VibeCADProcess = Get-ExpectedVibeCADProcess

if (-not ("VibeCADVisibleOperator.VibeCADVirtualCursor" -as [type])) {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $OperatorSource = @"
using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace VibeCADVisibleOperator
{
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }

    public static class NativeWindow
    {
        public const int SW_SHOWNOACTIVATE = 4;

        [DllImport("user32.dll")]
        public static extern bool GetWindowRect(IntPtr window, out RECT rect);

        [DllImport("user32.dll")]
        public static extern uint GetWindowThreadProcessId(
            IntPtr window,
            out uint processId
        );

        [DllImport("user32.dll")]
        public static extern bool IsIconic(IntPtr window);

        [DllImport("user32.dll")]
        public static extern bool ShowWindow(IntPtr window, int command);

        [DllImport("user32.dll")]
        public static extern bool SetWindowPos(
            IntPtr window,
            IntPtr insertAfter,
            int x,
            int y,
            int width,
            int height,
            uint flags
        );
    }

    public sealed class VibeCADVirtualCursor : Form
    {
        private bool pressed;

        public VibeCADVirtualCursor()
        {
            FormBorderStyle = FormBorderStyle.None;
            ShowInTaskbar = false;
            StartPosition = FormStartPosition.Manual;
            TopMost = true;
            BackColor = Color.Fuchsia;
            TransparencyKey = Color.Fuchsia;
            ClientSize = new Size(38, 52);
            SetStyle(
                ControlStyles.UserPaint |
                ControlStyles.AllPaintingInWmPaint |
                ControlStyles.OptimizedDoubleBuffer,
                true
            );
        }

        protected override bool ShowWithoutActivation { get { return true; } }

        protected override CreateParams CreateParams
        {
            get
            {
                CreateParams value = base.CreateParams;
                const int WS_EX_TRANSPARENT = 0x00000020;
                const int WS_EX_TOOLWINDOW = 0x00000080;
                const int WS_EX_NOACTIVATE = 0x08000000;
                value.ExStyle |= WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE;
                return value;
            }
        }

        public void ShowVirtualCursor()
        {
            Show();
            IntPtr topMost = new IntPtr(-1);
            const uint SWP_NOSIZE = 0x0001;
            const uint SWP_NOMOVE = 0x0002;
            const uint SWP_NOACTIVATE = 0x0010;
            const uint SWP_SHOWWINDOW = 0x0040;
            NativeWindow.SetWindowPos(
                Handle,
                topMost,
                0,
                0,
                0,
                0,
                SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW
            );
            Application.DoEvents();
        }

        public void MoveToScreen(int screenX, int screenY)
        {
            Location = new Point(screenX - 2, screenY - 2);
            Invalidate();
            Application.DoEvents();
        }

        public void SetPressed(bool value)
        {
            pressed = value;
            Invalidate();
            Application.DoEvents();
        }

        protected override void OnPaint(PaintEventArgs eventArgs)
        {
            base.OnPaint(eventArgs);
            Graphics graphics = eventArgs.Graphics;
            graphics.SmoothingMode = SmoothingMode.AntiAlias;
            int offset = pressed ? 2 : 0;
            Point[] pointer = new Point[] {
                new Point(2 + offset, 2 + offset),
                new Point(3 + offset, 36 + offset),
                new Point(12 + offset, 27 + offset),
                new Point(20 + offset, 47 + offset),
                new Point(29 + offset, 43 + offset),
                new Point(20 + offset, 24 + offset),
                new Point(34 + offset, 24 + offset)
            };
            using (SolidBrush pointerFill = new SolidBrush(Color.Cyan))
            {
                graphics.FillPolygon(pointerFill, pointer);
            }
        }
    }
}
"@
    $TrustedPlatformAssemblies = [string][AppContext]::GetData(
        "TRUSTED_PLATFORM_ASSEMBLIES"
    )
    if ($TrustedPlatformAssemblies) {
        $OperatorReferences = @(
            $TrustedPlatformAssemblies.Split(
                [IO.Path]::PathSeparator,
                [StringSplitOptions]::RemoveEmptyEntries
            )
        )
        $OperatorReferences += [System.Windows.Forms.Form].Assembly.Location
        $OperatorReferences += [System.Drawing.Graphics].Assembly.Location
        Add-Type `
            -ReferencedAssemblies @($OperatorReferences | Sort-Object -Unique) `
            -TypeDefinition $OperatorSource
    }
    else {
        Add-Type `
            -ReferencedAssemblies @("System.Windows.Forms", "System.Drawing") `
            -TypeDefinition $OperatorSource
    }
}

function Get-VibeCADWindowBounds {
    param([Parameter(Mandatory = $true)][long] $WindowHandle)
    if ($WindowHandle -le 0) {
        throw "VibeCAD did not report a valid QMainWindow handle."
    }
    [VibeCADVisibleOperator.RECT]$Bounds = New-Object VibeCADVisibleOperator.RECT
    $Window = [IntPtr]$WindowHandle
    [uint32]$OwnerProcessId = 0
    [VibeCADVisibleOperator.NativeWindow]::GetWindowThreadProcessId(
        $Window,
        [ref]$OwnerProcessId
    ) | Out-Null
    if ([int]$OwnerProcessId -ne $VibeCADProcess.Id) {
        throw "Qt window handle $WindowHandle does not belong to exact VibeCAD PID $($VibeCADProcess.Id)."
    }
    if (-not [VibeCADVisibleOperator.NativeWindow]::GetWindowRect($Window, [ref]$Bounds)) {
        throw "Windows could not report the exact VibeCAD window bounds."
    }
    return $Bounds
}

function Restore-VibeCADWindowIfMinimized {
    param([Parameter(Mandatory = $true)][long] $WindowHandle)
    # Validate ownership before changing any window state. This restores only
    # the exact Qt window reported by the authenticated checkout process.
    $null = Get-VibeCADWindowBounds -WindowHandle $WindowHandle
    $Window = [IntPtr]$WindowHandle
    if (-not [VibeCADVisibleOperator.NativeWindow]::IsIconic($Window)) {
        return $false
    }
    [VibeCADVisibleOperator.NativeWindow]::ShowWindow(
        $Window,
        [VibeCADVisibleOperator.NativeWindow]::SW_SHOWNOACTIVATE
    ) | Out-Null
    $Stopwatch = [Diagnostics.Stopwatch]::StartNew()
    do {
        Start-Sleep -Milliseconds 50
        if (-not [VibeCADVisibleOperator.NativeWindow]::IsIconic($Window)) {
            return $true
        }
    }
    while ($Stopwatch.Elapsed.TotalSeconds -lt $VerificationTimeoutSeconds)
    throw "The exact VibeCAD window remained minimized after SW_SHOWNOACTIVATE."
}

function Assert-TargetInsideExactWindow {
    param(
        [Parameter(Mandatory = $true)][int] $X,
        [Parameter(Mandatory = $true)][int] $Y,
        [Parameter(Mandatory = $true)][long] $WindowHandle
    )
    $Bounds = Get-VibeCADWindowBounds -WindowHandle $WindowHandle
    if (
        $X -lt $Bounds.Left -or
        $X -ge $Bounds.Right -or
        $Y -lt $Bounds.Top -or
        $Y -ge $Bounds.Bottom
    ) {
        throw "Semantic target ($X,$Y) is outside the exact VibeCAD window."
    }
}

function Get-VibeCADUiSnapshot {
    param([Parameter(Mandatory = $true)][string] $Kind)
    $Route = if ($Kind -eq "menu") { "/v1/ui/menus" } else { "/v1/ui/ribbon" }
    $Snapshot = Invoke-VibeCADAgentGet -Route $Route
    if (-not $Snapshot.ok) {
        throw "VibeCAD $Kind snapshot failed: $($Snapshot.failure_code): $($Snapshot.error)"
    }
    if (-not $Snapshot.visible -or [int]$Snapshot.process_id -ne $VibeCADProcess.Id) {
        throw "The live $Kind target is not visible or belongs to another process."
    }
    return $Snapshot
}

function Get-VibeCADDefaultTourTargets {
    $MenuSnapshot = Get-VibeCADUiSnapshot -Kind "menu"
    $RibbonSnapshot = Get-VibeCADUiSnapshot -Kind "ribbon"
    $DefaultTargets = [Collections.Generic.List[string]]::new()

    foreach ($Menu in @($MenuSnapshot.menus)) {
        if ($Menu.enabled -and $Menu.visible -and [string]$Menu.text) {
            $DefaultTargets.Add("menu:$([string]$Menu.text)")
        }
    }
    foreach ($Tab in @($RibbonSnapshot.tabs)) {
        if ($Tab.enabled -and [string]$Tab.text) {
            $DefaultTargets.Add("ribbon:$([string]$Tab.text)")
        }
    }
    return @($DefaultTargets)
}

if (-not $Targets -or @($Targets).Count -eq 0) {
    $Targets = @(Get-VibeCADDefaultTourTargets)
}
if (@($Targets).Count -eq 0) {
    throw "The live VibeCAD window exposed no enabled menu or ribbon targets."
}

function Resolve-VibeCADUiTarget {
    param([Parameter(Mandatory = $true)][string] $Specification)
    $Parts = @($Specification.Split(@(":"), 2, [StringSplitOptions]::None))
    if ($Parts.Count -eq 1) {
        $Kind = "ribbon"
        $Text = [string]$Parts[0]
    }
    else {
        $Kind = ([string]$Parts[0]).Trim().ToLowerInvariant()
        $Text = ([string]$Parts[1]).Trim()
    }
    if ($Kind -in @("tab", "ribbon_tab")) {
        $Kind = "ribbon"
    }
    if ($Kind -notin @("ribbon", "menu") -or -not $Text) {
        throw "Invalid target '$Specification'; use menu:Name or ribbon:Name."
    }
    $Snapshot = Get-VibeCADUiSnapshot -Kind $Kind
    $Items = if ($Kind -eq "menu") { @($Snapshot.menus) } else { @($Snapshot.tabs) }
    $Matches = @($Items | Where-Object { [string]$_.text -eq $Text })
    if ($Matches.Count -ne 1) {
        throw "Expected one enabled $Kind target named '$Text'; found $($Matches.Count)."
    }
    $Item = $Matches[0]
    if (-not $Item.enabled -or ($Kind -eq "menu" -and -not $Item.visible)) {
        throw "Semantic $Kind target '$Text' is disabled or hidden."
    }
    return [pscustomobject]@{
        Kind = $Kind
        Text = $Text
        Index = [int]$Item.index
        X = [int]$Item.screen_rect.center_x
        Y = [int]$Item.screen_rect.center_y
        WindowHandle = [long]$Snapshot.window_handle
        Snapshot = $Snapshot
    }
}

function Wait-WithVirtualCursor {
    param(
        [Parameter(Mandatory = $true)][int] $Milliseconds,
        [Parameter(Mandatory = $true)] $Cursor
    )
    $Remaining = [Math]::Max(0, $Milliseconds)
    while ($Remaining -gt 0) {
        [Windows.Forms.Application]::DoEvents()
        $Slice = [Math]::Min(20, $Remaining)
        Start-Sleep -Milliseconds $Slice
        $Remaining -= $Slice
    }
    [Windows.Forms.Application]::DoEvents()
}

function Move-VibeCADVirtualCursor {
    param(
        [Parameter(Mandatory = $true)][int] $X,
        [Parameter(Mandatory = $true)][int] $Y,
        [Parameter(Mandatory = $true)] $Cursor,
        [Parameter(Mandatory = $true)] $State
    )
    $StartX = [int]$State.X
    $StartY = [int]$State.Y
    $Steps = [Math]::Max(1, [int][Math]::Ceiling($MoveDurationMilliseconds / 16.0))
    for ($Step = 1; $Step -le $Steps; $Step += 1) {
        $Progress = $Step / [double]$Steps
        $Smooth = $Progress * $Progress * (3.0 - (2.0 * $Progress))
        $CurrentX = [int][Math]::Round($StartX + (($X - $StartX) * $Smooth))
        $CurrentY = [int][Math]::Round($StartY + (($Y - $StartY) * $Smooth))
        $Cursor.MoveToScreen($CurrentX, $CurrentY)
        Start-Sleep -Milliseconds 16
    }
    $State.X = $X
    $State.Y = $Y
}

function Wait-ForSemanticTarget {
    param(
        [Parameter(Mandatory = $true)][string] $Kind,
        [Parameter(Mandatory = $true)][string] $Text,
        [Parameter(Mandatory = $true)] $Cursor
    )
    $Stopwatch = [Diagnostics.Stopwatch]::StartNew()
    do {
        $Snapshot = Get-VibeCADUiSnapshot -Kind $Kind
        if ($Kind -eq "ribbon") {
            if ([string]$Snapshot.selected_text -eq $Text) {
                return $Snapshot
            }
        }
        else {
            $Menu = @($Snapshot.menus | Where-Object { [string]$_.text -eq $Text })
            if ($Menu.Count -eq 1 -and $Menu[0].menu_visible) {
                return $Snapshot
            }
        }
        Wait-WithVirtualCursor -Milliseconds 100 -Cursor $Cursor
    }
    while ($Stopwatch.Elapsed.TotalSeconds -lt $VerificationTimeoutSeconds)
    throw "Timed out waiting for semantic $Kind target '$Text'."
}

$InitialSnapshot = Get-VibeCADUiSnapshot -Kind "ribbon"
if (
    Restore-VibeCADWindowIfMinimized `
        -WindowHandle ([long]$InitialSnapshot.window_handle)
) {
    $InitialSnapshot = Get-VibeCADUiSnapshot -Kind "ribbon"
}
$Bounds = Get-VibeCADWindowBounds -WindowHandle ([long]$InitialSnapshot.window_handle)
$CursorState = [pscustomobject]@{
    X = [int]($Bounds.Left + 24)
    Y = [int]($Bounds.Top + 84)
}
$Receipts = [Collections.Generic.List[object]]::new()
$VirtualCursor = New-Object VibeCADVisibleOperator.VibeCADVirtualCursor
$VirtualCursor.MoveToScreen($CursorState.X, $CursorState.Y)
$VirtualCursor.ShowVirtualCursor()

try {
    $Sequence = 0
    foreach ($RequestedTarget in @($Targets)) {
        $Target = Resolve-VibeCADUiTarget -Specification ([string]$RequestedTarget)
        Assert-TargetInsideExactWindow `
            -X $Target.X `
            -Y $Target.Y `
            -WindowHandle $Target.WindowHandle
        Move-VibeCADVirtualCursor `
            -X $Target.X `
            -Y $Target.Y `
            -Cursor $VirtualCursor `
            -State $CursorState
        Wait-WithVirtualCursor -Milliseconds $DwellMilliseconds -Cursor $VirtualCursor

        $VirtualCursor.SetPressed($true)
        Wait-WithVirtualCursor -Milliseconds 120 -Cursor $VirtualCursor
        $Click = Invoke-VibeCADAgentPost -Route "/v1/ui/click" -Body @{
            kind = $Target.Kind
            text = $Target.Text
            expected_index = $Target.Index
            expected_process_id = $VibeCADProcess.Id
        }
        Wait-WithVirtualCursor -Milliseconds 140 -Cursor $VirtualCursor
        $VirtualCursor.SetPressed($false)

        if (
            -not $Click.ok -or
            (-not $Click.semantic_verified -and -not $Click.click_queued)
        ) {
            throw "Virtual click failed for $($Target.Kind):$($Target.Text): $($Click.failure_code): $($Click.error)"
        }
        if (
            [string]$Click.input_method -notin @(
                "qt_in_process_mouse_click",
                "qt_in_process_menu_popup"
            ) -or
            [string]$Click.physical_cursor_control -ne "none"
        ) {
            throw "The UI click did not use the independent in-process Qt path."
        }
        foreach ($RestorationField in @(
            "focus_restored",
            "active_window_unchanged",
            "popup_restored",
            "active_action_restored",
            "interaction_restored"
        )) {
            $RestorationValue = $Click.PSObject.Properties[$RestorationField].Value
            if ($RestorationValue -isnot [bool] -or -not [bool]$RestorationValue) {
                throw "The UI click did not restore $RestorationField for $($Target.Kind):$($Target.Text)."
            }
        }
        if ($Target.Kind -eq "menu") {
            if (
                $Click.menu_visible -isnot [bool] -or
                -not [bool]$Click.menu_visible -or
                $Click.menu_open_after -isnot [bool] -or
                [bool]$Click.menu_open_after -or
                [int]$Click.preview_duration_milliseconds -lt 200
            ) {
                throw "The menu preview was not visibly activated and closed before control returned."
            }
            $Verified = Get-VibeCADUiSnapshot -Kind "menu"
            $VerifiedMenu = @(
                $Verified.menus |
                    Where-Object { [string]$_.text -eq $Target.Text }
            )
            if (
                $VerifiedMenu.Count -ne 1 -or
                [bool]$VerifiedMenu[0].menu_visible
            ) {
                throw "The menu preview remained open after interaction restoration."
            }
        }
        else {
            $Verified = Wait-ForSemanticTarget `
                -Kind $Target.Kind `
                -Text $Target.Text `
                -Cursor $VirtualCursor
        }
        Wait-WithVirtualCursor `
            -Milliseconds $AfterClickDwellMilliseconds `
            -Cursor $VirtualCursor

        $Sequence += 1
        $Receipts.Add([pscustomobject]@{
            sequence = $Sequence
            target_kind = $Target.Kind
            requested_text = $Target.Text
            semantic_index = $Target.Index
            screen_x = $Target.X
            screen_y = $Target.Y
            selected_text = if ($Target.Kind -eq "ribbon") { [string]$Verified.selected_text } else { $null }
            menu_visible = if ($Target.Kind -eq "menu") { $true } else { $null }
            exact_process_id = $VibeCADProcess.Id
            input_method = [string]$Click.input_method
            physical_cursor_control = [string]$Click.physical_cursor_control
            physical_cursor_unchanged_during_click = [bool]$Click.physical_cursor_unchanged
            virtual_cursor_color = "cyan"
            semantic_verified = $true
            verified_at_utc = [DateTime]::UtcNow.ToString("o")
        })
    }
}
finally {
    if ($null -ne $VirtualCursor) {
        $VirtualCursor.SetPressed($false)
        $VirtualCursor.Close()
        $VirtualCursor.Dispose()
    }
}

if (-not $ReceiptPath) {
    $ReceiptPath = New-VibeCADTourReceiptPath -RepositoryRoot $RepositoryRoot
}
elseif (-not [IO.Path]::IsPathRooted($ReceiptPath)) {
    $ReceiptPath = Join-Path $RepositoryRoot $ReceiptPath
}
$ResolvedReceiptPath = [IO.Path]::GetFullPath($ReceiptPath)
$Payload = New-VibeCADTourReceiptPayload `
    -ProcessId $VibeCADProcess.Id `
    -Executable $ExpectedExecutable `
    -Targets @($Receipts) `
    -ReceiptPath $ResolvedReceiptPath
$ReceiptJson = Write-VibeCADTourReceipt `
    -Payload $Payload `
    -ReceiptPath $ResolvedReceiptPath
$ReceiptJson
