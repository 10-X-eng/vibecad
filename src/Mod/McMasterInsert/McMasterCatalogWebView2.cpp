// SPDX-License-Identifier: LGPL-2.1-or-later

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>
#include <wrl.h>

#include <WebView2.h>

#include <filesystem>
#include <string>
#include <system_error>
#include <vector>

using Microsoft::WRL::Callback;
using Microsoft::WRL::ComPtr;

namespace
{
constexpr wchar_t WindowClass[] = L"VibeCADMcMasterWebView2";
constexpr wchar_t WindowTitle[] = L"Insert McMaster-Carr Component - VibeCAD";
constexpr wchar_t InstanceMutex[] = L"Local\\VibeCADMcMasterCatalogWebView2";
constexpr wchar_t RuntimeDownloadUrl[] =
    L"https://go.microsoft.com/fwlink/p/?LinkId=2124703";
constexpr int ToolbarHeight = 44;
constexpr int StatusHeight = 28;
constexpr int ButtonBack = 1001;
constexpr int ButtonForward = 1002;
constexpr int ButtonReload = 1003;
constexpr int ButtonExternal = 1004;
constexpr UINT_PTR ParentTimer = 2001;
constexpr UINT_PTR CloseAfterDownloadTimer = 2002;

HWND windowHandle = nullptr;
HWND backButton = nullptr;
HWND forwardButton = nullptr;
HWND reloadButton = nullptr;
HWND externalButton = nullptr;
HWND statusLabel = nullptr;
HANDLE instanceMutex = nullptr;
DWORD parentProcessId = 0;
std::filesystem::path inboxPath;
std::wstring profilePath;
std::wstring initialUrl = L"https://www.mcmaster.com/";
ComPtr<ICoreWebView2Controller> webViewController;
ComPtr<ICoreWebView2> webView;

void setStatus(const std::wstring& text)
{
    if (statusLabel) {
        SetWindowTextW(statusLabel, text.c_str());
    }
}

void showWebViewError(const std::wstring& message)
{
    setStatus(message);
    MessageBoxW(windowHandle, message.c_str(), L"VibeCAD McMaster Catalog", MB_OK | MB_ICONERROR);
}

std::wstring argumentValue(const std::vector<std::wstring>& arguments, const wchar_t* name)
{
    const std::wstring option(name);
    const std::wstring prefix = option + L"=";
    for (std::size_t index = 0; index < arguments.size(); ++index) {
        const auto& argument = arguments[index];
        if (argument == option && index + 1 < arguments.size()) {
            return arguments[index + 1];
        }
        if (argument.compare(0, prefix.size(), prefix) == 0) {
            return argument.substr(prefix.size());
        }
    }
    return {};
}

bool hasArgument(const std::vector<std::wstring>& arguments, const wchar_t* name)
{
    for (const auto& argument : arguments) {
        if (argument == name) {
            return true;
        }
    }
    return false;
}

bool downloadPathAvailable(const std::filesystem::path& path)
{
    std::error_code error;
    auto staging = path;
    staging += L".download";
    return !std::filesystem::exists(path, error)
        && !std::filesystem::exists(staging, error);
}

std::filesystem::path uniqueDownloadPath(const std::filesystem::path& suggested)
{
    std::filesystem::path filename = suggested.filename();
    if (filename.empty() || filename == L"." || filename == L"..") {
        filename = L"McMaster-CAD.step";
    }
    std::filesystem::path result = inboxPath / filename;
    if (downloadPathAvailable(result)) {
        return result;
    }
    const auto stem = filename.stem().wstring();
    const auto extension = filename.extension().wstring();
    for (unsigned int suffix = 1; suffix < 10000; ++suffix) {
        result = inboxPath / (stem + L"-" + std::to_wstring(suffix) + extension);
        if (downloadPathAvailable(result)) {
            return result;
        }
    }
    return inboxPath / (stem + L"-" + std::to_wstring(GetTickCount64()) + extension);
}

void updateHistoryButtons()
{
    if (!webView) {
        return;
    }
    BOOL canGoBack = FALSE;
    BOOL canGoForward = FALSE;
    webView->get_CanGoBack(&canGoBack);
    webView->get_CanGoForward(&canGoForward);
    EnableWindow(backButton, canGoBack);
    EnableWindow(forwardButton, canGoForward);
}

void resizeWebView()
{
    if (!windowHandle) {
        return;
    }
    RECT client{};
    GetClientRect(windowHandle, &client);
    const int width = client.right - client.left;
    const int height = client.bottom - client.top;
    const int padding = 8;
    const int smallButtonWidth = 74;
    const int externalButtonWidth = 132;

    MoveWindow(backButton, padding, 7, smallButtonWidth, 30, TRUE);
    MoveWindow(forwardButton, padding + 80, 7, smallButtonWidth, 30, TRUE);
    MoveWindow(reloadButton, padding + 160, 7, smallButtonWidth, 30, TRUE);
    MoveWindow(
        externalButton,
        width - externalButtonWidth - padding,
        7,
        externalButtonWidth,
        30,
        TRUE
    );
    MoveWindow(
        statusLabel,
        padding,
        height - StatusHeight,
        width - 2 * padding,
        StatusHeight,
        TRUE
    );

    if (webViewController) {
        RECT bounds{0, ToolbarHeight, width, height - StatusHeight};
        webViewController->put_Bounds(bounds);
    }
}

void openCurrentPageExternally()
{
    std::wstring url = initialUrl;
    if (webView) {
        LPWSTR source = nullptr;
        if (SUCCEEDED(webView->get_Source(&source)) && source) {
            url = source;
            CoTaskMemFree(source);
        }
    }
    ShellExecuteW(windowHandle, L"open", url.c_str(), nullptr, nullptr, SW_SHOWNORMAL);
}

HRESULT registerWebViewEvents()
{
    EventRegistrationToken token{};
    HRESULT result = webView->add_NavigationStarting(
        Callback<ICoreWebView2NavigationStartingEventHandler>(
            [](ICoreWebView2*, ICoreWebView2NavigationStartingEventArgs*) -> HRESULT {
                setStatus(L"Loading McMaster-Carr...");
                return S_OK;
            }
        ).Get(),
        &token
    );
    if (FAILED(result)) {
        return result;
    }
    result = webView->add_NavigationCompleted(
        Callback<ICoreWebView2NavigationCompletedEventHandler>(
            [](ICoreWebView2*, ICoreWebView2NavigationCompletedEventArgs* args) -> HRESULT {
                BOOL success = FALSE;
                args->get_IsSuccess(&success);
                setStatus(
                    success
                        ? L"Choose a part and download 3-D STEP; VibeCAD imports it automatically."
                        : L"McMaster-Carr did not load. Reload or use Open in Browser."
                );
                updateHistoryButtons();
                return S_OK;
            }
        ).Get(),
        &token
    );
    if (FAILED(result)) {
        return result;
    }
    result = webView->add_HistoryChanged(
        Callback<ICoreWebView2HistoryChangedEventHandler>(
            [](ICoreWebView2*, IUnknown*) -> HRESULT {
                updateHistoryButtons();
                return S_OK;
            }
        ).Get(),
        &token
    );
    if (FAILED(result)) {
        return result;
    }
    ComPtr<ICoreWebView2_4> downloadWebView;
    result = webView.As(&downloadWebView);
    if (FAILED(result)) {
        return result;
    }
    return downloadWebView->add_DownloadStarting(
        Callback<ICoreWebView2DownloadStartingEventHandler>(
            [](ICoreWebView2*, ICoreWebView2DownloadStartingEventArgs* args) -> HRESULT {
                LPWSTR originalPath = nullptr;
                args->get_ResultFilePath(&originalPath);
                const auto target = uniqueDownloadPath(
                    originalPath ? std::filesystem::path(originalPath)
                                 : std::filesystem::path(L"McMaster-CAD.step")
                );
                CoTaskMemFree(originalPath);
                auto staging = target;
                staging += L".download";

                HRESULT pathResult = args->put_ResultFilePath(staging.c_str());
                if (FAILED(pathResult)) {
                    setStatus(L"Could not send the download to VibeCAD's McMaster inbox.");
                    return pathResult;
                }
                args->put_Handled(TRUE);
                setStatus(L"Downloading " + target.filename().wstring() + L"...");

                ComPtr<ICoreWebView2DownloadOperation> operation;
                if (SUCCEEDED(args->get_DownloadOperation(&operation)) && operation) {
                    EventRegistrationToken stateToken{};
                    operation->add_StateChanged(
                        Callback<ICoreWebView2StateChangedEventHandler>(
                            [staging, target](
                                ICoreWebView2DownloadOperation* sender,
                                IUnknown*
                            ) -> HRESULT {
                                COREWEBVIEW2_DOWNLOAD_STATE state{};
                                sender->get_State(&state);
                                if (state == COREWEBVIEW2_DOWNLOAD_STATE_COMPLETED) {
                                    std::error_code moveError;
                                    std::filesystem::rename(staging, target, moveError);
                                    if (moveError) {
                                        setStatus(
                                            L"Download completed, but VibeCAD could not move it "
                                            L"into the McMaster inbox."
                                        );
                                    }
                                    else {
                                        setStatus(
                                            L"Downloaded " + target.filename().wstring()
                                            + L". VibeCAD is importing it now."
                                        );
                                        SetTimer(
                                            windowHandle,
                                            CloseAfterDownloadTimer,
                                            1200,
                                            nullptr
                                        );
                                    }
                                }
                                else if (state == COREWEBVIEW2_DOWNLOAD_STATE_INTERRUPTED) {
                                    std::error_code removeError;
                                    std::filesystem::remove(staging, removeError);
                                    setStatus(L"The McMaster CAD download was interrupted.");
                                }
                                return S_OK;
                            }
                        ).Get(),
                        &stateToken
                    );
                }
                return S_OK;
            }
        ).Get(),
        &token
    );
}

void initializeWebView()
{
    setStatus(L"Starting Microsoft Edge WebView2...");
    HRESULT result = CreateCoreWebView2EnvironmentWithOptions(
        nullptr,
        profilePath.c_str(),
        nullptr,
        Callback<ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler>(
            [](HRESULT environmentResult, ICoreWebView2Environment* environment) -> HRESULT {
                if (FAILED(environmentResult) || !environment) {
                    showWebViewError(
                        L"Microsoft Edge WebView2 Runtime is unavailable. "
                        L"Install the Evergreen Runtime and try again."
                    );
                    return environmentResult;
                }
                return environment->CreateCoreWebView2Controller(
                    windowHandle,
                    Callback<ICoreWebView2CreateCoreWebView2ControllerCompletedHandler>(
                        [](
                            HRESULT controllerResult,
                            ICoreWebView2Controller* controller
                        ) -> HRESULT {
                            if (FAILED(controllerResult) || !controller) {
                                showWebViewError(
                                    L"VibeCAD could not create the WebView2 catalog window."
                                );
                                return controllerResult;
                            }
                            webViewController = controller;
                            HRESULT coreResult = controller->get_CoreWebView2(&webView);
                            if (FAILED(coreResult) || !webView) {
                                showWebViewError(
                                    L"VibeCAD could not initialize the WebView2 browser."
                                );
                                return coreResult;
                            }
                            ComPtr<ICoreWebView2Settings> settings;
                            if (SUCCEEDED(webView->get_Settings(&settings)) && settings) {
                                settings->put_IsStatusBarEnabled(FALSE);
                                settings->put_AreDevToolsEnabled(FALSE);
                            }
                            HRESULT eventResult = registerWebViewEvents();
                            if (FAILED(eventResult)) {
                                showWebViewError(
                                    L"VibeCAD could not attach WebView2 browser events."
                                );
                                return eventResult;
                            }
                            resizeWebView();
                            updateHistoryButtons();
                            return webView->Navigate(initialUrl.c_str());
                        }
                    ).Get()
                );
            }
        ).Get()
    );
    if (FAILED(result)) {
        showWebViewError(L"VibeCAD could not start the Microsoft Edge WebView2 Runtime.");
    }
}

bool parentIsRunning()
{
    if (!parentProcessId) {
        return true;
    }
    HANDLE process = OpenProcess(SYNCHRONIZE, FALSE, parentProcessId);
    if (!process) {
        return false;
    }
    const bool running = WaitForSingleObject(process, 0) == WAIT_TIMEOUT;
    CloseHandle(process);
    return running;
}

LRESULT CALLBACK windowProcedure(HWND handle, UINT message, WPARAM wParam, LPARAM lParam)
{
    switch (message) {
    case WM_CREATE: {
        HFONT font = static_cast<HFONT>(GetStockObject(DEFAULT_GUI_FONT));
        backButton = CreateWindowW(
            L"BUTTON", L"Back", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON,
            0, 0, 0, 0, handle,
            reinterpret_cast<HMENU>(static_cast<INT_PTR>(ButtonBack)), nullptr, nullptr
        );
        forwardButton = CreateWindowW(
            L"BUTTON", L"Forward", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON,
            0, 0, 0, 0, handle,
            reinterpret_cast<HMENU>(static_cast<INT_PTR>(ButtonForward)), nullptr, nullptr
        );
        reloadButton = CreateWindowW(
            L"BUTTON", L"Reload", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON,
            0, 0, 0, 0, handle,
            reinterpret_cast<HMENU>(static_cast<INT_PTR>(ButtonReload)), nullptr, nullptr
        );
        externalButton = CreateWindowW(
            L"BUTTON", L"Open in Browser", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON,
            0, 0, 0, 0, handle,
            reinterpret_cast<HMENU>(static_cast<INT_PTR>(ButtonExternal)), nullptr, nullptr
        );
        statusLabel = CreateWindowW(
            L"STATIC", L"Starting McMaster-Carr...", WS_CHILD | WS_VISIBLE | SS_LEFT,
            0, 0, 0, 0, handle, nullptr, nullptr, nullptr
        );
        for (HWND control : {
                 backButton,
                 forwardButton,
                 reloadButton,
                 externalButton,
                 statusLabel,
             }) {
            SendMessageW(control, WM_SETFONT, reinterpret_cast<WPARAM>(font), TRUE);
        }
        SetTimer(handle, ParentTimer, 2000, nullptr);
        return 0;
    }
    case WM_SIZE:
        resizeWebView();
        return 0;
    case WM_COMMAND:
        switch (LOWORD(wParam)) {
        case ButtonBack:
            if (webView) {
                webView->GoBack();
            }
            break;
        case ButtonForward:
            if (webView) {
                webView->GoForward();
            }
            break;
        case ButtonReload:
            if (webView) {
                webView->Reload();
            }
            break;
        case ButtonExternal:
            openCurrentPageExternally();
            break;
        default:
            break;
        }
        return 0;
    case WM_TIMER:
        if (wParam == ParentTimer && !parentIsRunning()) {
            DestroyWindow(handle);
        }
        else if (wParam == CloseAfterDownloadTimer) {
            KillTimer(handle, CloseAfterDownloadTimer);
            DestroyWindow(handle);
        }
        return 0;
    case WM_DPICHANGED: {
        const auto* suggested = reinterpret_cast<RECT*>(lParam);
        SetWindowPos(
            handle,
            nullptr,
            suggested->left,
            suggested->top,
            suggested->right - suggested->left,
            suggested->bottom - suggested->top,
            SWP_NOACTIVATE | SWP_NOZORDER
        );
        return 0;
    }
    case WM_DESTROY:
        KillTimer(handle, ParentTimer);
        KillTimer(handle, CloseAfterDownloadTimer);
        if (webViewController) {
            webViewController->Close();
        }
        webView.Reset();
        webViewController.Reset();
        PostQuitMessage(0);
        return 0;
    default:
        return DefWindowProcW(handle, message, wParam, lParam);
    }
}

bool webView2RuntimeAvailable()
{
    LPWSTR version = nullptr;
    const HRESULT result = GetAvailableCoreWebView2BrowserVersionString(nullptr, &version);
    CoTaskMemFree(version);
    return SUCCEEDED(result);
}
}  // namespace

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int commandShow)
{
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    if (FAILED(CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED))) {
        return 1;
    }

    int argumentCount = 0;
    LPWSTR* rawArguments = CommandLineToArgvW(GetCommandLineW(), &argumentCount);
    std::vector<std::wstring> arguments;
    for (int index = 0; rawArguments && index < argumentCount; ++index) {
        arguments.emplace_back(rawArguments[index]);
    }
    LocalFree(rawArguments);

    if (hasArgument(arguments, L"--smoke-test")) {
        const bool available = webView2RuntimeAvailable();
        CoUninitialize();
        return available ? 0 : 2;
    }

    inboxPath = argumentValue(arguments, L"--inbox");
    profilePath = argumentValue(arguments, L"--profile");
    if (hasArgument(arguments, L"--argument-parser-smoke-test")) {
        const bool parsedRequiredPaths = !inboxPath.empty() && !profilePath.empty();
        CoUninitialize();
        return parsedRequiredPaths ? 0 : 4;
    }
    const auto requestedUrl = argumentValue(arguments, L"--url");
    if (!requestedUrl.empty()) {
        initialUrl = requestedUrl;
    }
    const auto parent = argumentValue(arguments, L"--parent-pid");
    if (!parent.empty()) {
        try {
            parentProcessId = static_cast<DWORD>(std::stoul(parent));
        }
        catch (...) {
            parentProcessId = 0;
        }
    }
    if (inboxPath.empty() || profilePath.empty()) {
        MessageBoxW(
            nullptr,
            L"VibeCAD did not provide the McMaster inbox and browser profile paths.",
            L"VibeCAD McMaster Catalog",
            MB_OK | MB_ICONERROR
        );
        CoUninitialize();
        return 3;
    }

    std::error_code directoryError;
    std::filesystem::create_directories(inboxPath, directoryError);
    std::filesystem::create_directories(profilePath, directoryError);

    instanceMutex = CreateMutexW(nullptr, TRUE, InstanceMutex);
    if (instanceMutex && GetLastError() == ERROR_ALREADY_EXISTS) {
        if (HWND existing = FindWindowW(WindowClass, nullptr)) {
            ShowWindow(existing, SW_RESTORE);
            SetForegroundWindow(existing);
        }
        CloseHandle(instanceMutex);
        CoUninitialize();
        return 0;
    }

    WNDCLASSEXW windowClass{};
    windowClass.cbSize = sizeof(windowClass);
    windowClass.hInstance = instance;
    windowClass.lpfnWndProc = windowProcedure;
    windowClass.lpszClassName = WindowClass;
    windowClass.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    windowClass.hIcon = LoadIconW(instance, MAKEINTRESOURCEW(101));
    windowClass.hIconSm = windowClass.hIcon;
    windowClass.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
    if (!RegisterClassExW(&windowClass)) {
        CloseHandle(instanceMutex);
        CoUninitialize();
        return 4;
    }

    windowHandle = CreateWindowExW(
        0,
        WindowClass,
        WindowTitle,
        WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT,
        CW_USEDEFAULT,
        1180,
        820,
        nullptr,
        nullptr,
        instance,
        nullptr
    );
    if (!windowHandle) {
        CloseHandle(instanceMutex);
        CoUninitialize();
        return 5;
    }
    ShowWindow(windowHandle, commandShow);
    UpdateWindow(windowHandle);

    if (!webView2RuntimeAvailable()) {
        const int choice = MessageBoxW(
            windowHandle,
            L"Microsoft Edge WebView2 Runtime is required for the VibeCAD McMaster catalog. "
            L"Open Microsoft's installer page now?",
            L"VibeCAD McMaster Catalog",
            MB_YESNO | MB_ICONINFORMATION
        );
        if (choice == IDYES) {
            ShellExecuteW(
                windowHandle, L"open", RuntimeDownloadUrl, nullptr, nullptr, SW_SHOWNORMAL
            );
        }
        DestroyWindow(windowHandle);
    }
    else {
        initializeWebView();
    }

    MSG message{};
    while (GetMessageW(&message, nullptr, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }

    if (instanceMutex) {
        ReleaseMutex(instanceMutex);
        CloseHandle(instanceMutex);
    }
    CoUninitialize();
    return static_cast<int>(message.wParam);
}
