import AppKit
import WebKit

/// Embed WKWebView in a Qt NSView. No extra NSWindow collectionBehavior.

final class Host: NSObject, WKNavigationDelegate, WKDownloadDelegate, WKUIDelegate {
    let webView: WKWebView
    let outDir: URL
    let cadExtensions: Set<String> = [
        "step", "stp", "stpz", "iges", "igs", "sat", "sab", "x_t", "x_b", "sldprt", "zip",
    ]

    init(parent: NSView, outDir: URL) {
        self.outDir = outDir
        parent.wantsLayer = true
        let config = WKWebViewConfiguration()
        config.websiteDataStore = WKWebsiteDataStore.default()
        config.defaultWebpagePreferences.allowsContentJavaScript = true
        config.preferences.javaScriptCanOpenWindowsAutomatically = true
        let webView = WKWebView(frame: parent.bounds, configuration: config)
        webView.autoresizingMask = [.width, .height]
        webView.translatesAutoresizingMaskIntoConstraints = false
        self.webView = webView
        super.init()
        webView.navigationDelegate = self
        webView.uiDelegate = self
        parent.addSubview(webView)
        NSLayoutConstraint.activate([
            webView.topAnchor.constraint(equalTo: parent.topAnchor),
            webView.bottomAnchor.constraint(equalTo: parent.bottomAnchor),
            webView.leadingAnchor.constraint(equalTo: parent.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: parent.trailingAnchor),
        ])
        log("attach bounds=\(parent.bounds) window=\(String(describing: parent.window))")
        DispatchQueue.main.async { [weak self] in
            self?.loadCatalog()
        }
    }

    func loadCatalog() {
        let url = URL(string: "https://www.mcmaster.com/")!
        let request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 45)
        log("load \(url.absoluteString) webView.bounds=\(webView.bounds)")
        webView.load(request)
        setStatus("loading")
    }

    func isCAD(_ response: URLResponse) -> Bool {
        let name = response.suggestedFilename?.lowercased() ?? ""
        let ext = (name as NSString).pathExtension
        if cadExtensions.contains(ext) { return true }
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
        if isCAD(navigationResponse.response), #available(macOS 11.3, *) {
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
        log("download \(safe)")
        completionHandler(dest)
    }

    @available(macOS 11.3, *)
    func downloadDidFinish(_ download: WKDownload) {
        log("download finished")
    }

    @available(macOS 11.3, *)
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        log("download failed \(error)")
    }

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        setStatus("loading")
        log("didStart \(webView.url?.absoluteString ?? "")")
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        setStatus("ok \(webView.url?.host ?? "")")
        log("didFinish \(webView.url?.absoluteString ?? "")")
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        handleFail(error)
    }

    func webView(
        _ webView: WKWebView,
        didFailProvisionalNavigation navigation: WKNavigation!,
        withError error: Error
    ) {
        handleFail(error)
    }

    func handleFail(_ error: Error) {
        let nsError = error as NSError
        if nsError.domain == NSURLErrorDomain && nsError.code == NSURLErrorCancelled {
            return
        }
        setStatus("error \(nsError.localizedDescription)")
        log("load fail \(error)")
    }

    func webView(
        _ webView: WKWebView,
        createWebViewWith configuration: WKWebViewConfiguration,
        for navigationAction: WKNavigationAction,
        windowFeatures: WKWindowFeatures
    ) -> WKWebView? {
        if let url = navigationAction.request.url {
            webView.load(URLRequest(url: url))
        }
        return nil
    }
}

private var hosts: [ObjectIdentifier: Host] = [:]
private var lastStatus = "idle"
private let statusRaw: UnsafeMutablePointer<CChar> = {
    let pointer = UnsafeMutablePointer<CChar>.allocate(capacity: 1024)
    pointer.initialize(repeating: 0, count: 1024)
    return pointer
}()

private func log(_ message: String) {
    NSLog("McMasterWebKit \(message)")
    lastStatus = message
    let dir = FileManager.default.urls(for: .libraryDirectory, in: .userDomainMask).first?
        .appendingPathComponent("Logs", isDirectory: true)
    guard let dir else { return }
    try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    let file = dir.appendingPathComponent("McMasterWebKit.log")
    let line = "\(Date()) \(message)\n"
    guard let data = line.data(using: .utf8) else { return }
    if FileManager.default.fileExists(atPath: file.path),
       let handle = try? FileHandle(forWritingTo: file)
    {
        handle.seekToEndOfFile()
        handle.write(data)
        try? handle.close()
    } else {
        try? data.write(to: file)
    }
}

private func setStatus(_ value: String) {
    lastStatus = value
    let chars = Array(value.utf8CString.prefix(1023))
    for i in 0..<1024 {
        statusRaw[i] = i < chars.count ? chars[i] : 0
    }
}

@_cdecl("McMasterWebKit_Attach")
public func McMasterWebKit_Attach(
    _ nsviewPtr: UnsafeMutableRawPointer?,
    _ outDirC: UnsafePointer<CChar>?
) -> Int32 {
    guard let nsviewPtr, let outDirC else {
        log("attach missing pointer")
        return 1
    }
    let view = Unmanaged<NSView>.fromOpaque(nsviewPtr).takeUnretainedValue()
    let outDir = URL(fileURLWithPath: String(cString: outDirC), isDirectory: true)
    try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)
    let work = {
        if hosts[ObjectIdentifier(view)] != nil {
            hosts[ObjectIdentifier(view)]?.loadCatalog()
            return
        }
        hosts[ObjectIdentifier(view)] = Host(parent: view, outDir: outDir)
    }
    if Thread.isMainThread {
        work()
    } else {
        DispatchQueue.main.sync(execute: work)
    }
    return 0
}

@_cdecl("McMasterWebKit_Status")
public func McMasterWebKit_Status() -> UnsafePointer<CChar>? {
    UnsafePointer(statusRaw)
}
