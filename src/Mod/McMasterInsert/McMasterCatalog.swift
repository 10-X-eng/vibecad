import Cocoa
import CoreGraphics
import WebKit

/// Overlay catalog for VibeCAD. Runs as a separate process so WebKit cannot
/// crash VibeCAD. Intercepts CAD downloads (no Save dialog) and writes them
/// into --out-dir for VibeCAD to import.

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKDownloadDelegate, WKUIDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var status: NSTextField!
    var outDir: URL
    var loadAttempts = 0
    let cadExtensions: Set<String> = [
        "step", "stp", "stpz", "iges", "igs", "sat", "sab", "x_t", "x_b", "sldprt", "zip",
    ]

    init(outDir: URL) {
        self.outDir = outDir
        super.init()
    }

    static let raiseName = Notification.Name("com.vibecad.McMasterCatalog.raise")
    static let catalogURL = URL(string: "https://www.mcmaster.com/")!

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildUI()
    }

    func buildUI() {
        guard window == nil else {
            raiseCatalog()
            return
        }
        try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)

        DistributedNotificationCenter.default().addObserver(
            forName: AppDelegate.raiseName,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            self?.raiseCatalog()
        }

        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 80, y: 80, width: 1200, height: 800)
        let width = min(1040, screen.width - 120)
        let height = min(740, screen.height - 120)
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: width, height: height),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "McMaster-Carr Catalog"
        window.isReleasedWhenClosed = false
        window.hidesOnDeactivate = false
        window.level = NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.popUpMenuWindow)))
        window.collectionBehavior = [
            .canJoinAllSpaces,
            .fullScreenAuxiliary,
        ]
        window.center()

        let root = NSView(frame: NSRect(x: 0, y: 0, width: width, height: height))
        status = NSTextField(labelWithString: "Loading McMaster-Carr…")
        status.translatesAutoresizingMaskIntoConstraints = false
        status.textColor = .secondaryLabelColor
        root.addSubview(status)

        let config = WKWebViewConfiguration()
        config.websiteDataStore = WKWebsiteDataStore.default()
        config.defaultWebpagePreferences.allowsContentJavaScript = true
        config.preferences.javaScriptCanOpenWindowsAutomatically = true
        webView = WKWebView(frame: .zero, configuration: config)
        webView.translatesAutoresizingMaskIntoConstraints = false
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.customUserAgent =
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15"
        root.addSubview(webView)
        NSLayoutConstraint.activate([
            status.leadingAnchor.constraint(equalTo: root.leadingAnchor, constant: 12),
            status.trailingAnchor.constraint(equalTo: root.trailingAnchor, constant: -12),
            status.topAnchor.constraint(equalTo: root.topAnchor, constant: 6),
            webView.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            webView.topAnchor.constraint(equalTo: status.bottomAnchor, constant: 6),
            webView.bottomAnchor.constraint(equalTo: root.bottomAnchor),
        ])
        window.contentView = root
        raiseCatalog()
        loadCatalog()
        for delay in [0.15, 0.4, 0.9, 1.6, 2.5] {
            DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
                self?.raiseCatalog()
            }
        }
        NSLog("McMasterCatalog window shown")
    }

    func loadCatalog() {
        loadAttempts += 1
        status.stringValue = "Loading McMaster-Carr…"
        let request = URLRequest(
            url: AppDelegate.catalogURL,
            cachePolicy: .reloadIgnoringLocalCacheData,
            timeoutInterval: 30
        )
        webView.load(request)
    }

    func raiseCatalog() {
        window.collectionBehavior.insert(.canJoinAllSpaces)
        window.collectionBehavior.insert(.fullScreenAuxiliary)
        window.level = NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.popUpMenuWindow)))
        window.orderFrontRegardless()
        window.makeKeyAndOrderFront(nil)
        if #available(macOS 14.0, *) {
            NSApp.activate()
        } else {
            NSApp.activate(ignoringOtherApps: true)
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func isCADResponse(_ response: URLResponse) -> Bool {
        let name = response.suggestedFilename?.lowercased() ?? ""
        let ext = (name as NSString).pathExtension
        if cadExtensions.contains(ext) { return true }
        if let mime = response.mimeType?.lowercased(),
           mime.contains("step") || mime.contains("iges") || mime.contains("acad")
        {
            return true
        }
        if let url = response.url, cadExtensions.contains(url.pathExtension.lowercased()) {
            return true
        }
        return false
    }

    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationAction: WKNavigationAction,
        decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
    ) {
        if let url = navigationAction.request.url,
           cadExtensions.contains(url.pathExtension.lowercased()),
           #available(macOS 11.3, *)
        {
            decisionHandler(.download)
            return
        }
        decisionHandler(.allow)
    }

    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationResponse: WKNavigationResponse,
        decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void
    ) {
        if isCADResponse(navigationResponse.response), #available(macOS 11.3, *) {
            decisionHandler(.download)
            return
        }
        decisionHandler(.allow)
    }

    @available(macOS 11.3, *)
    func webView(
        _ webView: WKWebView,
        navigationAction: WKNavigationAction,
        didBecome download: WKDownload
    ) {
        download.delegate = self
    }

    @available(macOS 11.3, *)
    func webView(
        _ webView: WKWebView,
        navigationResponse: WKNavigationResponse,
        didBecome download: WKDownload
    ) {
        download.delegate = self
    }

    @available(macOS 11.3, *)
    func download(
        _ download: WKDownload,
        decideDestinationUsing response: URLResponse,
        suggestedFilename: String,
        completionHandler: @escaping (URL?) -> Void
    ) {
        let safe = suggestedFilename.replacingOccurrences(of: "/", with: "_")
        let dest = outDir.appendingPathComponent(safe)
        try? FileManager.default.removeItem(at: dest)
        completionHandler(dest)
    }

    @available(macOS 11.3, *)
    func downloadDidFinish(_ download: WKDownload) {}

    @available(macOS 11.3, *)
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {}

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        status.stringValue = "McMaster-Carr — download 3-D STEP to import into VibeCAD"
        raiseCatalog()
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        handleLoadFailure(error)
    }

    func webView(
        _ webView: WKWebView,
        didFailProvisionalNavigation navigation: WKNavigation!,
        withError error: Error
    ) {
        handleLoadFailure(error)
    }

    func handleLoadFailure(_ error: Error) {
        let nsError = error as NSError
        if nsError.domain == NSURLErrorDomain && nsError.code == NSURLErrorCancelled {
            return
        }
        NSLog("McMasterCatalog load failed: \(error)")
        status.stringValue = "Could not load McMaster-Carr (\(nsError.localizedDescription)). Retrying…"
        if loadAttempts < 3 {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { [weak self] in
                self?.loadCatalog()
            }
        } else {
            status.stringValue = "Could not reach McMaster-Carr. Check the network, then click Catalog again."
        }
    }

    func webView(
        _ webView: WKWebView,
        createWebViewWith configuration: WKWebViewConfiguration,
        for navigationAction: WKNavigationAction,
        windowFeatures: WKWindowFeatures
    ) -> WKWebView? {
        if navigationAction.targetFrame == nil, let url = navigationAction.request.url {
            webView.load(URLRequest(url: url))
        }
        return nil
    }
}

let args = CommandLine.arguments
var out = FileManager.default.temporaryDirectory.appendingPathComponent("McMasterInbox")
if let idx = args.firstIndex(of: "--out-dir"), idx + 1 < args.count {
    out = URL(fileURLWithPath: args[idx + 1], isDirectory: true)
}

let logDir = FileManager.default.urls(for: .libraryDirectory, in: .userDomainMask).first?
    .appendingPathComponent("Logs", isDirectory: true)
if let logDir {
    try? FileManager.default.createDirectory(at: logDir, withIntermediateDirectories: true)
    let logFile = logDir.appendingPathComponent("McMasterCatalog.log")
    let line = "launch \(Date()) out=\(out.path)\n"
    if let data = line.data(using: .utf8) {
        if FileManager.default.fileExists(atPath: logFile.path) {
            if let handle = try? FileHandle(forWritingTo: logFile) {
                handle.seekToEndOfFile()
                handle.write(data)
                try? handle.close()
            }
        } else {
            try? data.write(to: logFile)
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = AppDelegate(outDir: out)
app.delegate = delegate
delegate.buildUI()
app.finishLaunching()
app.run()
