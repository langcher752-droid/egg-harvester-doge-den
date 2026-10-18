import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var mainWindow: MainWindowController!
    private var accountWindow: AccountWindowController?
    private var servicesWindow: ServicesWindowController?
    private var statusItem: NSStatusItem!
    private var statusMenu = NSMenu()

    // MARK: - 生命周期

    // 双击 Dock 图标 / `open -a` 时把主窗口拉到前台
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if let w = mainWindow?.window {
            w.makeKeyAndOrderFront(nil)
        }
        NSApp.activate(ignoringOtherApps: true)
        return true
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()

        mainWindow = MainWindowController()
        mainWindow.show()

        ServiceSupervisor.shared.onChange = { [weak self] in
            self?.refreshStatusItem()
            self?.servicesWindow?.reload()
        }

        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "--account-image"), i + 1 < args.count {
            let path = args[i + 1]
            let aw = AccountWindowController()
            aw.showWindow(nil)
            DispatchQueue.main.asyncAfter(deadline: .now() + 4) {
                let ok = aw.renderPNG(to: path)
                print("[account-image] \(ok ? "OK" : "FAIL") -> \(path)")
                exit(ok ? 0 : 1)
            }
        } else if let i = args.firstIndex(of: "--toast-image"), i + 1 < args.count {
            let path = args[i + 1]
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
                ToastCenter.shared.show(emoji: "🎁",
                                        title: "重要情报示例：新站注册送额度",
                                        body: "该站提供 gpt-5.6-luna，注册即送 20 美元额度，支持每日签到领取更多。")
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) {
                    let ok = ToastCenter.shared.renderTop(to: path)
                    print("[toast-image] \(ok ? "OK" : "FAIL") -> \(path)")
                    exit(ok ? 0 : 1)
                }
            }
        } else if let i = args.firstIndex(of: "--eval"), i + 1 < args.count {
            // 自检：在页面里执行 JS 并打印结果；支持 --goto <panel> 先切面板并等渲染
            let code = args[i + 1]
            var panel = ""
            if let g = args.firstIndex(of: "--goto"), g + 1 < args.count { panel = args[g + 1] }
            var extraWait = 0.0
            if let w = args.firstIndex(of: "--wait"), w + 1 < args.count { extraWait = (Double(args[w + 1]) ?? 0) / 1000.0 }
            DispatchQueue.main.asyncAfter(deadline: .now() + 9) {
                if panel == "qq" {
                    self.mainWindow.evaluate("if (typeof openQQModal === 'function') openQQModal()")
                } else if !panel.isEmpty {
                    self.mainWindow.evaluate("if (typeof switchView === 'function') switchView('\(panel)')")
                }
                DispatchQueue.main.asyncAfter(deadline: .now() + (panel.isEmpty ? 0 : 2.5) + extraWait) {
                    self.mainWindow.evaluateResult(code) { v in
                        print("[eval] \(v ?? "nil")")
                        exit(0)
                    }
                }
            }
        } else if let i = args.firstIndex(of: "--snapshot"), i + 1 < args.count {
            // 自检模式：只截图，不接管后台服务；可选 --goto <panel> 先切面板
            let path = args[i + 1]
            var panel = ""
            if let g = args.firstIndex(of: "--goto"), g + 1 < args.count { panel = args[g + 1] }
            var js = ""
            if let j = args.firstIndex(of: "--js"), j + 1 < args.count { js = args[j + 1] }
            var extraWait = 0.0
            if let w = args.firstIndex(of: "--wait"), w + 1 < args.count { extraWait = (Double(args[w + 1]) ?? 0) / 1000.0 }
            DispatchQueue.main.asyncAfter(deadline: .now() + 9) {
                if panel == "qq" {
                    self.mainWindow.evaluate("if (typeof openQQModal === 'function') openQQModal()")
                } else if !panel.isEmpty {
                    self.mainWindow.evaluate("if (typeof switchView === 'function') switchView('\(panel)')")
                }
                if !js.isEmpty {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { self.mainWindow.evaluate(js) }
                }
                DispatchQueue.main.asyncAfter(deadline: .now() + (panel.isEmpty ? 0.4 : 1.8) + (js.isEmpty ? 0 : 2.2) + extraWait) {
                    self.mainWindow.snapshot(to: path) { ok in
                        print("[snapshot] \(ok ? "OK" : "FAIL") -> \(path)" + (panel.isEmpty ? "" : " panel=\(panel)"))
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { exit(ok ? 0 : 1) }
                    }
                }
            }
        } else {
            ServiceSupervisor.shared.startSupervision()
            // 首启若 QQ 未登录，自动把「账号与登录」窗口推到前台，提示扫码（只弹一次）
            DispatchQueue.main.asyncAfter(deadline: .now() + 8) { [weak self] in
                guard let self = self else { return }
                if !ServiceSupervisor.shared.qqOnline {
                    self.showAccount()
                    print("[login] QQ 未登录，已自动打开账号与登录窗口（扫码后会自动记录账号，今后自动登录）")
                }
            }
        }

        NotifierBridge.shared.start()
        ensureLaunchAgent(enable: AppConfig.shared.cfg.autoStartApp)
        refreshStatusItem()

        // 首个 tick 后再刷一次状态
        DispatchQueue.main.asyncAfter(deadline: .now() + 6) { [weak self] in
            ServiceSupervisor.shared.refresh()
            self?.refreshStatusItem()
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    func applicationWillTerminate(_ notification: Notification) {
        ServiceSupervisor.shared.shutdownOwned()
    }

    // MARK: - 菜单

    private func buildMenu() {
        let mainMenu = NSMenu()

        // 应用菜单
        let appItem = NSMenuItem()
        mainMenu.addItem(appItem)
        let appMenu = NSMenu()
        appItem.submenu = appMenu
        appMenu.addItem(withTitle: "关于 鸡蛋收割机", action: #selector(showAbout), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "账号与登录…", action: #selector(showAccount), keyEquivalent: ",")
        appMenu.addItem(withTitle: "服务状态…", action: #selector(showServices), keyEquivalent: "s")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "隐藏 鸡蛋收割机", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "退出 鸡蛋收割机", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")

        // 编辑菜单（没有它 WKWebView 里 Cmd+C / Cmd+V / Cmd+A 都不响应）
        let editItem = NSMenuItem()
        mainMenu.addItem(editItem)
        let editMenu = NSMenu(title: "编辑")
        editItem.submenu = editMenu
        editMenu.addItem(withTitle: "撤销", action: Selector(("undo:")), keyEquivalent: "z")
        editMenu.addItem(withTitle: "重做", action: Selector(("redo:")), keyEquivalent: "Z")
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "剪切", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "拷贝", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "粘贴", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "粘贴为纯文本", action: #selector(NSTextView.pasteAsPlainText(_:)), keyEquivalent: "V")
        editMenu.addItem(withTitle: "全选", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "查找…", action: #selector(NSResponder.performTextFinderAction(_:)), keyEquivalent: "f")

        // 视图菜单
        let viewItem = NSMenuItem()
        mainMenu.addItem(viewItem)
        let viewMenu = NSMenu(title: "视图")
        viewItem.submenu = viewMenu
        viewMenu.addItem(withTitle: "重新加载", action: #selector(reloadPage), keyEquivalent: "r")
        viewMenu.addItem(withTitle: "强制重新加载", action: #selector(hardReloadPage), keyEquivalent: "R")
        viewMenu.addItem(.separator())
        for (title, hash) in [("鸡蛋库", ""), ("情报流", "stream"), ("简报", "intel"), ("群文件", "files"), ("群管理", "groups"), ("供应商体检", "providers"), ("Doge 动画", "doge"), ("系统设置", "settings")] {
            let it = NSMenuItem(title: title, action: #selector(gotoPanel(_:)), keyEquivalent: "")
            it.representedObject = hash
            viewMenu.addItem(it)
        }
        viewMenu.addItem(.separator())
        viewMenu.addItem(.separator())
        viewMenu.addItem(withTitle: "打开数据目录", action: #selector(openDataDir), keyEquivalent: "")
        viewMenu.addItem(withTitle: "打开日志目录", action: #selector(openLogsDir), keyEquivalent: "")
        viewMenu.addItem(withTitle: "在浏览器中打开", action: #selector(openInBrowser), keyEquivalent: "")

        // 服务菜单
        let svcItem = NSMenuItem()
        mainMenu.addItem(svcItem)
        let svcMenu = NSMenu(title: "服务")
        svcItem.submenu = svcMenu
        svcMenu.addItem(withTitle: "启动全部服务", action: #selector(startAllServices), keyEquivalent: "")
        svcMenu.addItem(withTitle: "重启全部服务", action: #selector(restartAllServices), keyEquivalent: "")
        svcMenu.addItem(.separator())
        svcMenu.addItem(withTitle: "服务状态…", action: #selector(showServices), keyEquivalent: "")
        svcMenu.addItem(withTitle: "QQ 扫码登录…", action: #selector(showAccount), keyEquivalent: "l")
        svcMenu.addItem(.separator())
        svcMenu.addItem(withTitle: "测试提醒卡片", action: #selector(testToast), keyEquivalent: "")
        svcMenu.addItem(withTitle: "通知权限设置…", action: #selector(openNotificationSettings), keyEquivalent: "")
        svcMenu.addItem(.separator())
        let auto = NSMenuItem(title: "开机自启", action: #selector(toggleAutoStart(_:)), keyEquivalent: "")
        auto.state = AppConfig.shared.cfg.autoStartApp ? .on : .off
        svcMenu.addItem(auto)

        // 窗口菜单
        let winItem = NSMenuItem()
        mainMenu.addItem(winItem)
        let winMenu = NSMenu(title: "窗口")
        winItem.submenu = winMenu
        winMenu.addItem(withTitle: "主窗口", action: #selector(showMain), keyEquivalent: "1")
        winMenu.addItem(withTitle: "最小化", action: #selector(NSWindow.miniaturize(_:)), keyEquivalent: "m")
        NSApp.windowsMenu = winMenu

        NSApp.mainMenu = mainMenu
    }

    private func refreshStatusItem() {
        if statusItem == nil {
            statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        }
        let sup = ServiceSupervisor.shared
        let up = sup.services.filter { $0.state.isUp }.count
        let total = max(sup.services.count, 1)
        statusItem.button?.title = up == total ? "🥚 \(up)/\(total)" : "🥚 \(up)/\(total)"

        let menu = NSMenu()
        let head = NSMenuItem(title: "\(up)/\(total) 服务在线", action: nil, keyEquivalent: "")
        head.isEnabled = false
        menu.addItem(head)
        menu.addItem(.separator())
        for s in sup.services {
            let item = NSMenuItem(title: "\(s.state.dot) \(s.title) — \(s.stateLabel)", action: nil, keyEquivalent: "")
            item.isEnabled = false
            menu.addItem(item)
        }
        menu.addItem(.separator())
        menu.addItem(withTitle: "显示主窗口", action: #selector(showMain), keyEquivalent: "")
        menu.addItem(withTitle: "QQ 扫码登录…", action: #selector(showAccount), keyEquivalent: "")
        menu.addItem(withTitle: "重启全部服务", action: #selector(restartAllServices), keyEquivalent: "")
        menu.addItem(.separator())
        menu.addItem(withTitle: "退出", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "")
        statusItem.menu = menu
    }

    // MARK: - 动作

    @objc private func showMain() { mainWindow?.show() }

    @objc private func showAccount() {
        if accountWindow == nil { accountWindow = AccountWindowController() }
        accountWindow?.showWindow(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc private func showServices() {
        if servicesWindow == nil { servicesWindow = ServicesWindowController() }
        servicesWindow?.showWindow(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc private func reloadPage() { mainWindow?.reload(nil) }
    @objc private func hardReloadPage() { mainWindow?.hardReload(nil) }


    @objc private func gotoPanel(_ sender: NSMenuItem) {
        let panel = (sender.representedObject as? String) ?? ""
        let name = panel.isEmpty ? "keys" : panel
        mainWindow?.show()
        mainWindow?.evaluate("if (typeof switchView === 'function') { switchView('\(name)'); } else { location.hash = '\(name)'; }")
    }

    @objc private func openDataDir() {
        NSWorkspace.shared.open(AppConfig.shared.backendURL)
    }

    @objc private func openLogsDir() {
        try? FileManager.default.createDirectory(at: AppConfig.shared.logsDir, withIntermediateDirectories: true)
        NSWorkspace.shared.open(AppConfig.shared.logsDir)
    }

    @objc private func openInBrowser() {
        NSWorkspace.shared.open(AppConfig.shared.baseURL)
    }

    @objc private func startAllServices() {
        ServiceSupervisor.shared.setSupervising(true)
        ServiceSupervisor.shared.ensureAllRunning()
    }

    @objc private func restartAllServices() {
        ServiceSupervisor.shared.restartAll()
        mainWindow?.show()
    }

    @objc private func toggleAutoStart(_ sender: NSMenuItem) {
        let enable = sender.state != .on
        ensureLaunchAgent(enable: enable)
        sender.state = enable ? .on : .off
        AppConfig.shared.update { $0.autoStartApp = enable }
    }

    @objc private func testToast() {
        NotifierBridge.shared.testToast()
    }

    @objc private func openNotificationSettings() {
        let urls = [
            "x-apple.systempreferences:com.apple.Notifications-Settings.extension",
            "x-apple.systempreferences:com.apple.preference.notifications"
        ]
        for s in urls {
            if let u = URL(string: s), NSWorkspace.shared.open(u) { return }
        }
    }

    @objc private func showAbout() {
        let a = NSAlert()
        a.messageText = "鸡蛋收割机 · 群情报"
        a.informativeText = """
        双源采集（QQ / 微信 · 网页）· 自动分类 · 情报简报 · 群文件决策

        后端端口：\(AppConfig.shared.cfg.port)
        OneBot：127.0.0.1:3000
        数据目录：\(AppConfig.shared.cfg.backendRoot)
        """
        a.addButton(withTitle: "好")
        a.runModal()
    }

    // MARK: - 开机自启

    /// 只有安装在 ~/Applications 或 /Applications 时才写开机自启，避免测试构建污染登录项
    private var isInstalledLocation: Bool {
        let p = Bundle.main.bundlePath
        return p.hasPrefix(NSHomeDirectory() + "/Applications/") || p.hasPrefix("/Applications/")
    }

    private func ensureLaunchAgent(enable: Bool) {
        let url = AppConfig.shared.launchAgentAppURL
        guard isInstalledLocation else {
            NSLog("[autostart] 跳过（非安装位置：\(Bundle.main.bundlePath)）")
            return
        }
        let fm = FileManager.default
        if !enable {
            if fm.fileExists(atPath: url.path) {
                _ = Shell.run("/bin/launchctl", ["bootout", "gui/\(getuid())/website.lanche.groupintel-app"], timeout: 10)
                try? fm.removeItem(at: url)
            }
            return
        }
        let plist = """
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key><string>website.lanche.groupintel-app</string>
            <key>ProgramArguments</key>
            <array>
                <string>/usr/bin/open</string>
                <string>-a</string>
                <string>\(Bundle.main.bundlePath)</string>
            </array>
            <key>RunAtLoad</key><true/>
            <key>KeepAlive</key><false/>
            <key>ProcessType</key><string>Interactive</string>
            <key>StandardOutPath</key><string>\(AppConfig.shared.logsDir.path)/app.out.log</string>
            <key>StandardErrorPath</key><string>\(AppConfig.shared.logsDir.path)/app.err.log</string>
        </dict>
        </plist>
        """
        try? fm.createDirectory(at: AppConfig.shared.logsDir, withIntermediateDirectories: true)
        try? plist.write(to: url, atomically: true, encoding: .utf8)
        _ = Shell.run("/bin/launchctl", ["bootout", "gui/\(getuid())/website.lanche.groupintel-app"], timeout: 10)
        _ = Shell.run("/bin/launchctl", ["bootstrap", "gui/\(getuid())", url.path], timeout: 10)
    }
}
