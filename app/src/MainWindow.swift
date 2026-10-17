import AppKit
import WebKit

final class MainWindowController: NSWindowController, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate, WKScriptMessageHandler {
    private var webView: WKWebView!
    private var splash: NSView!
    private var splashLabel: NSTextField!
    private var splashSpinner: NSProgressIndicator!
    private var readyTimer: Timer?

    convenience init() {
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1200, height: 800),
            styleMask: [.titled, .closable, .resizable, .miniaturizable],
            backing: .buffered,
            defer: false)
        window.title = "鸡蛋收割机 · 群情报"
        window.minSize = NSSize(width: 1020, height: 640)
        window.titlebarAppearsTransparent = false
        window.center()
        window.isReleasedWhenClosed = false
        window.setFrameAutosaveName("GroupIntelMainWindow2")   // v2：默认更小的尺寸
        self.init(window: window)

        let cfg = WKWebViewConfiguration()
        cfg.preferences.setValue(true, forKey: "developerExtrasEnabled")
        let script = WKUserScript(source: "window.__GROUPINTEL_APP__ = true;",
                                  injectionTime: .atDocumentStart, forMainFrameOnly: true)
        cfg.userContentController.addUserScript(script)
        cfg.userContentController.add(WeakScriptHandler(handler: self), name: "reveal")

        webView = WKWebView(frame: window.contentView!.bounds, configuration: cfg)
        webView.autoresizingMask = [.width, .height]
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsMagnification = true
        webView.setValue(false, forKey: "drawsBackground")
        window.contentView!.addSubview(webView)

        buildSplash(in: window)

        AppState.shared.mainWindowController = self
        loadEntry()
    }

    // MARK: - 启动遮罩

    private func buildSplash(in window: NSWindow) {
        splash = NSView(frame: window.contentView!.bounds)
        splash.autoresizingMask = [.width, .height]
        splash.wantsLayer = true
        splash.layer?.backgroundColor = NSColor(calibratedWhite: 0.98, alpha: 1).cgColor

        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .centerX
        stack.spacing = 14
        stack.translatesAutoresizingMaskIntoConstraints = false

        let title = NSTextField(labelWithString: "🥚 鸡蛋收割机")
        title.font = .systemFont(ofSize: 30, weight: .bold)

        splashSpinner = NSProgressIndicator()
        splashSpinner.style = .spinning
        splashSpinner.controlSize = .large
        splashSpinner.startAnimation(nil)

        splashLabel = NSTextField(labelWithString: "正在启动本地服务…")
        splashLabel.font = .systemFont(ofSize: 13)
        splashLabel.textColor = .secondaryLabelColor

        stack.addArrangedSubview(title)
        stack.addArrangedSubview(splashSpinner)
        stack.addArrangedSubview(splashLabel)

        splash.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: splash.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: splash.centerYAnchor)
        ])
        window.contentView!.addSubview(splash)
    }

    private func hideSplash() {
        guard splash.superview != nil else { return }
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = 0.25
            splash.animator().alphaValue = 0
        } completionHandler: { [weak self] in
            self?.splash.removeFromSuperview()
        }
    }

    // MARK: - 加载

    func show(url: URL? = nil) {
        if let w = window, w.isMiniaturized { w.deminiaturize(nil) }
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        if let url = url { webView.load(URLRequest(url: url)) }
    }

    private func loadEntry() {
        readyTimer?.invalidate()
        let port = AppConfig.shared.cfg.port
        splashLabel.stringValue = "正在启动本地服务（端口 \(port)）…"

        readyTimer = Timer.scheduledTimer(withTimeInterval: 0.8, repeats: true) { [weak self] t in
            guard let self = self else { return }
            if Net.portOpen(host: "127.0.0.1", port: port) {
                t.invalidate()
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
                    self.splashLabel.stringValue = "服务已就绪，正在打开界面…"
                    var req = URLRequest(url: AppConfig.shared.baseURL)
                    req.timeoutInterval = 30
                    self.webView.load(req)
                }
            } else {
                let n = Int(Date().timeIntervalSince1970) % 3
                let dots = String(repeating: "·", count: n + 1)
                self.splashLabel.stringValue = "正在启动本地服务\(dots)"
            }
        }
        RunLoop.main.add(readyTimer!, forMode: .common)
    }

    func reload(_ sender: Any?) {
        webView.reload()
    }

    func hardReload(_ sender: Any?) {
        webView.reloadFromOrigin()
    }

    /// 自检用：把当前 WebView 渲染结果截图到文件
    func snapshot(to path: String, completion: @escaping (Bool) -> Void) {
        let cfg = WKSnapshotConfiguration()
        cfg.rect = webView.bounds
        webView.takeSnapshot(with: cfg) { image, err in
            guard let image = image,
                  let tiff = image.tiffRepresentation,
                  let rep = NSBitmapImageRep(data: tiff),
                  let png = rep.representation(using: .png, properties: [:]) else {
                NSLog("[snapshot] failed: \(err?.localizedDescription ?? "no image")")
                completion(false)
                return
            }
            let ok = (try? png.write(to: URL(fileURLWithPath: path))) != nil
            completion(ok)
        }
    }

    func evaluate(_ js: String) {
        webView.evaluateJavaScript(js, completionHandler: nil)
    }

    /// 执行 JS 并拿回结果（自检用）
    func evaluateResult(_ js: String, completion: @escaping (Any?) -> Void) {
        webView.evaluateJavaScript(js) { value, err in
            completion(err == nil ? value : "ERR: \(err!.localizedDescription)")
        }
    }

    // MARK: - WKNavigationDelegate

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        hideSplash()
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        showErrorIfNeeded(error)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        showErrorIfNeeded(error)
    }

    private func showErrorIfNeeded(_ error: Error) {
        let ns = error as NSError
        // 用户主动取消（如页面跳转中）忽略
        if ns.code == NSURLErrorCancelled { return }
        // 后端还没起来：重新走启动等待
        if ns.code == NSURLErrorCannotConnectToHost || ns.code == NSURLErrorNetworkConnectionLost {
            loadEntry()
            return
        }
        if splash.superview == nil { splash.alphaValue = 1; window?.contentView?.addSubview(splash) }
        splashSpinner.stopAnimation(nil)
        splashLabel.stringValue = "加载失败：\(ns.localizedDescription)（3 秒后重试）"
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in
            self?.splashSpinner.startAnimation(nil)
            self?.loadEntry()
        }
    }

    // 下载：blob / 附件类响应
    func webView(_ webView: WKWebView,
                 decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if navigationAction.shouldPerformDownload {
            decisionHandler(.download)
            return
        }
        // 外部链接（情报里的站点、API 文档等）走系统浏览器，不在内嵌页面里打开
        if let url = navigationAction.request.url, let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https" {
            let host = url.host ?? ""
            let isLocal = (host == "127.0.0.1" || host == "localhost")
            if !isLocal || url.port.map({ $0 != AppConfig.shared.cfg.port }) ?? false {
                NSWorkspace.shared.open(url)
                decisionHandler(.cancel)
                return
            }
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView,
                 decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        if !navigationResponse.canShowMIMEType {
            decisionHandler(.download)
            return
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    // 新窗口 → 系统浏览器
    func webView(_ webView: WKWebView,
                 createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction,
                 windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url {
            if url.scheme == "http" || url.scheme == "https" {
                NSWorkspace.shared.open(url)
            }
        }
        return nil
    }

    // MARK: - WKDownloadDelegate

    func download(_ download: WKDownload,
                  decideDestinationUsing response: URLResponse,
                  suggestedFilename: String,
                  completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = suggestedFilename
        panel.canCreateDirectories = true
        panel.directoryURL = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
        panel.begin { result in
            if result == .OK, let url = panel.url {
                completionHandler(url)
            } else {
                completionHandler(nil)
                download.cancel()
            }
        }
    }

    func downloadDidFinish(_ download: WKDownload) {
        NSSound(named: "Glass")?.play()
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        let a = NSAlert()
        a.messageText = "下载失败"
        a.informativeText = error.localizedDescription
        a.alertStyle = .warning
        a.runModal()
    }
}

// MARK: - JS → 原生

final class WeakScriptHandler: NSObject, WKScriptMessageHandler {
    weak var handler: WKScriptMessageHandler?
    init(handler: WKScriptMessageHandler) { self.handler = handler }
    func userContentController(_ c: WKUserContentController, didReceive message: WKScriptMessage) {
        handler?.userContentController(c, didReceive: message)
    }
}

extension MainWindowController {
    /// 网页里 window.webkit.messageHandlers.reveal.postMessage(path)
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == "reveal", let path = message.body as? String, !path.isEmpty else { return }
        let url = URL(fileURLWithPath: path)
        if FileManager.default.fileExists(atPath: path) {
            NSWorkspace.shared.activateFileViewerSelecting([url])
        } else {
            NSWorkspace.shared.open(url.deletingLastPathComponent())
        }
    }
}
