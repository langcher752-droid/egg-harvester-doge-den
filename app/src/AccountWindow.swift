import AppKit

/// 「账号与登录」窗口：QQ(NapCat) 扫码登录 + 微信凭据 + LLM 网关配置
final class AccountWindowController: NSWindowController, NSWindowDelegate {
    private var qqStatus = NSTextField(labelWithString: "检测中…")
    private var qrImage = NSImageView()
    private var qrHint = NSTextField(labelWithString: "")
    private var refreshTimer: Timer?

    private var wxidField = NSTextField()
    private var codeField = NSTextField()
    private var gwBaseField = NSTextField()
    private var gwKeyField = NSTextField()
    private var visionField = NSTextField()
    private var summaryField = NSTextField()
    private var saveHint = NSTextField(labelWithString: "")

    convenience init() {
        let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 720, height: 820),
                           styleMask: [.titled, .closable, .resizable],
                           backing: .buffered, defer: false)
        win.title = "账号与登录"
        win.isReleasedWhenClosed = false   // 由 ARC 管理，防止关闭时双重释放
        win.center()
        win.delegate = nil
        self.init(window: win)
        win.delegate = self
        buildUI()
        loadValues()
    }

    override func showWindow(_ sender: Any?) {
        super.showWindow(sender)
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        loadValues()
        refreshQQ()
        startTimer()
    }

    private func startTimer() {
        refreshTimer?.invalidate()
        refreshTimer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            guard let self = self, self.window?.isVisible == true else { return }
            self.refreshQQ()
        }
        RunLoop.main.add(refreshTimer!, forMode: .common)
    }

    func windowWillClose(_ notification: Notification) {
        refreshTimer?.invalidate()
        refreshTimer = nil
    }

    deinit { refreshTimer?.invalidate() }

    /// 自检：把窗口内容渲染成 PNG
    func renderPNG(to path: String) -> Bool {
        guard let view = window?.contentView,
              let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return false }
        view.layoutSubtreeIfNeeded()
        view.cacheDisplay(in: view.bounds, to: rep)
        guard let png = rep.representation(using: .png, properties: [:]) else { return false }
        return (try? png.write(to: URL(fileURLWithPath: path))) != nil
    }

    // MARK: - UI

    private func buildUI() {
        guard let content = window?.contentView else { return }
        let scroll = NSScrollView(frame: content.bounds)
        scroll.autoresizingMask = [.width, .height]
        scroll.hasVerticalScroller = true
        let doc = NSView()
        doc.translatesAutoresizingMaskIntoConstraints = false
        scroll.documentView = doc
        content.addSubview(scroll)

        let root = NSStackView()
        root.orientation = .vertical
        root.alignment = .leading
        root.spacing = 18
        root.translatesAutoresizingMaskIntoConstraints = false
        doc.addSubview(root)

        NSLayoutConstraint.activate([
            doc.widthAnchor.constraint(equalTo: scroll.widthAnchor),
            root.leadingAnchor.constraint(equalTo: doc.leadingAnchor, constant: 24),
            root.trailingAnchor.constraint(equalTo: doc.trailingAnchor, constant: -24),
            root.topAnchor.constraint(equalTo: doc.topAnchor, constant: 20),
            root.bottomAnchor.constraint(equalTo: doc.bottomAnchor, constant: -24)
        ])

        // ── QQ ─────────────────────────────────────────
        root.addArrangedSubview(section("QQ 登录（NapCat 协议端）"))
        qqStatus.font = .systemFont(ofSize: 13, weight: .medium)
        root.addArrangedSubview(qqStatus)

        qrImage.imageScaling = .scaleProportionallyUpOrDown
        qrImage.translatesAutoresizingMaskIntoConstraints = false
        qrImage.wantsLayer = true
        qrImage.layer?.borderColor = NSColor.separatorColor.cgColor
        qrImage.layer?.borderWidth = 1
        qrImage.layer?.cornerRadius = 8
        NSLayoutConstraint.activate([
            qrImage.widthAnchor.constraint(equalToConstant: 240),
            qrImage.heightAnchor.constraint(equalToConstant: 240)
        ])
        root.addArrangedSubview(qrImage)

        qrHint.font = .systemFont(ofSize: 11)
        qrHint.textColor = .secondaryLabelColor
        qrHint.maximumNumberOfLines = 3
        qrHint.preferredMaxLayoutWidth = 640
        root.addArrangedSubview(qrHint)

        let qqButtons = NSStackView()
        qqButtons.orientation = .horizontal
        qqButtons.spacing = 10
        qqButtons.addArrangedSubview(button("🔄 刷新二维码") { [weak self] in self?.regenQR() })
        qqButtons.addArrangedSubview(button("♻️ 重启 NapCat") { [weak self] in self?.restartNapCat() })
        qqButtons.addArrangedSubview(button("🌐 NapCat 管理页") {
            NSWorkspace.shared.open(URL(string: "http://127.0.0.1:6099/webui")!)
        })
        root.addArrangedSubview(qqButtons)

        // ── 微信 ────────────────────────────────────────
        root.addArrangedSubview(section("微信采集凭据"))
        wxidField = field(placeholder: "wxid_xxxxxxxx")
        codeField = field(placeholder: "数字 code，例如 123456789")
        let wxGrid = grid(rows: [("微信 wxid", wxidField), ("解密 Code", codeField)])
        root.addArrangedSubview(wxGrid)
        let wxTip = NSTextField(labelWithString: "提示：key = md5(code + wxid)[:16]，改完保存后微信采集会自动重启。")
        wxTip.font = .systemFont(ofSize: 11)
        wxTip.textColor = .secondaryLabelColor
        root.addArrangedSubview(wxTip)

        // ── LLM ────────────────────────────────────────
        root.addArrangedSubview(section("LLM 网关（分类 / 简报 / 图片识别）"))
        gwBaseField = field(placeholder: "https://router.example.com/v1")
        gwKeyField = field(placeholder: "sk-…")
        visionField = field(placeholder: "视觉模型名")
        summaryField = field(placeholder: "总结/分类模型名")
        let gwGrid = grid(rows: [
            ("GATEWAY_BASE", gwBaseField),
            ("GATEWAY_KEY", gwKeyField),
            ("VISION_MODEL", visionField),
            ("SUMMARY_MODEL", summaryField)
        ])
        root.addArrangedSubview(gwGrid)

        // ── 保存 ────────────────────────────────────────
        let saveRow = NSStackView()
        saveRow.orientation = .horizontal
        saveRow.spacing = 12
        saveRow.addArrangedSubview(button("💾 保存并重启采集", emphasized: true) { [weak self] in self?.save() })
        saveHint.font = .systemFont(ofSize: 12)
        saveHint.textColor = .systemGreen
        saveRow.addArrangedSubview(saveHint)
        root.addArrangedSubview(saveRow)
    }

    private func section(_ title: String) -> NSView {
        let l = NSTextField(labelWithString: title)
        l.font = .systemFont(ofSize: 15, weight: .semibold)
        return l
    }

    private func field(placeholder: String) -> NSTextField {
        let f = NSTextField()
        f.placeholderString = placeholder
        f.translatesAutoresizingMaskIntoConstraints = false
        f.widthAnchor.constraint(greaterThanOrEqualToConstant: 460).isActive = true
        return f
    }

    private func grid(rows: [(String, NSTextField)]) -> NSGridView {
        let g = NSGridView(views: rows.map { row in
            let l = NSTextField(labelWithString: row.0)
            l.font = .systemFont(ofSize: 12)
            l.textColor = .secondaryLabelColor
            l.setContentHuggingPriority(.defaultHigh, for: .horizontal)
            return [l, row.1] as [NSView]
        })
        g.rowSpacing = 8
        g.columnSpacing = 14
        g.translatesAutoresizingMaskIntoConstraints = false
        return g
    }

    private func button(_ title: String, emphasized: Bool = false, action: @escaping () -> Void) -> NSButton {
        let b = NSButton(title: title, target: self, action: #selector(buttonClicked(_:)))
        b.bezelStyle = .rounded
        b.onAction = action
        if emphasized {
            b.keyEquivalent = "\r"
            b.bezelColor = NSColor.systemBlue
            b.contentTintColor = .white
        }
        return b
    }

    // MARK: - 数据

    private func loadValues() {
        let env = AppConfig.shared.readEnv()
        wxidField.stringValue = env["WECHAT_WXID"] ?? ""
        codeField.stringValue = env["WECHAT_CODE"] ?? ""
        gwBaseField.stringValue = env["GATEWAY_BASE"] ?? ""
        gwKeyField.stringValue = env["GATEWAY_KEY"] ?? ""
        visionField.stringValue = env["VISION_MODEL"] ?? ""
        summaryField.stringValue = env["SUMMARY_MODEL"] ?? ""
    }

    private func save() {
        var updates: [String: String] = [:]
        if !wxidField.stringValue.isEmpty { updates["WECHAT_WXID"] = wxidField.stringValue.trimmingCharacters(in: .whitespaces) }
        if !codeField.stringValue.isEmpty { updates["WECHAT_CODE"] = codeField.stringValue.trimmingCharacters(in: .whitespaces) }
        if !gwBaseField.stringValue.isEmpty { updates["GATEWAY_BASE"] = gwBaseField.stringValue.trimmingCharacters(in: .whitespaces) }
        if !gwKeyField.stringValue.isEmpty { updates["GATEWAY_KEY"] = gwKeyField.stringValue.trimmingCharacters(in: .whitespaces) }
        if !visionField.stringValue.isEmpty { updates["VISION_MODEL"] = visionField.stringValue.trimmingCharacters(in: .whitespaces) }
        if !summaryField.stringValue.isEmpty { updates["SUMMARY_MODEL"] = summaryField.stringValue.trimmingCharacters(in: .whitespaces) }
        AppConfig.shared.writeEnv(updates)

        saveHint.stringValue = "已写入 .env，正在重启采集服务…"
        let sup = ServiceSupervisor.shared
        for s in sup.services where ["wechat-watch", "intel-watch", "web-watch", "backend"].contains(s.id) {
            sup.restart(s)
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) { [weak self] in
            self?.saveHint.stringValue = "已保存并重启 ✅"
        }
    }

    // MARK: - QQ 登录

    private func refreshQQ() {
        DispatchQueue.global(qos: .userInitiated).async {
            let loginInfo = Net.getJSON(AppConfig.shared.oneBotURL.appendingPathComponent("get_login_info"), timeout: 4) as? [String: Any]
            let data = loginInfo?["data"] as? [String: Any]
            let uid = (data?["user_id"] as? NSNumber)?.int64Value ?? 0
            let nick = data?["nickname"] as? String
            let online = uid > 0 && ((loginInfo?["retcode"] as? Int) == 0)

            if online { self.persistAutoLogin(uin: uid) }

            DispatchQueue.main.async {
                if online {
                    self.qqStatus.stringValue = "🟢 已登录：\(nick ?? "")（QQ \(uid)）"
                    self.qqStatus.textColor = .systemGreen
                    self.qrHint.stringValue = "QQ 已在线，消息会实时推送到本机 8890/onebot（已记住账号，重启自动登录）。"
                } else {
                    self.qqStatus.stringValue = "🟡 未登录 —— 请用手机 QQ 扫描下方二维码"
                    self.qqStatus.textColor = .systemOrange
                }
            }
            if !online { self.fetchQRImage() }
        }
    }

    /// QQ 登录成功后，把账号写进 NapCat 的 webui.json（autoLoginAccount），
    /// 这样容器/电脑重启后无需再扫码 —— 会话存在 /app/.config/QQ 卷里，配合自动登录即为「自动登录」。
    private func persistAutoLogin(uin: Int64) {
        guard uin > 0 else { return }
        let cfg = AppConfig.shared.cfg
        let file = "/app/napcat/config/webui.json"
        // 已经是该账号就跳过
        let (_, current) = Shell.run(cfg.dockerPath,
                                     ["exec", cfg.napcatContainer, "sh", "-c", "grep -o '\"autoLoginAccount\": *\"[0-9]*\"' \(file) 2>/dev/null"],
                                     timeout: 10)
        if current.contains("\(uin)") { return }
        let script = "sed -i 's/\"autoLoginAccount\": *\"[0-9]*\"/\"autoLoginAccount\": \"\(uin)\"/; s/\"autoLoginAccount\": *\"\"/\"autoLoginAccount\": \"\(uin)\"/' \(file)"
        let (code, out) = Shell.run(cfg.dockerPath, ["exec", cfg.napcatContainer, "sh", "-c", script], timeout: 10)
        if code == 0 {
            print("[account] 已记住 QQ \(uin) 用于自动登录（webui.json autoLoginAccount）")
        } else {
            print("[account] 写入 autoLoginAccount 失败: \(out)")
        }
    }

    private func fetchQRImage() {
        let cfg = AppConfig.shared.cfg
        // 先把容器内二维码拷到宿主机临时文件（docker cp 更可靠）
        let tmp = "/tmp/groupintel-qr.png"
        let (code, _) = Shell.run(cfg.dockerPath, ["cp", "\(cfg.napcatContainer):/app/napcat/cache/qrcode.png", tmp], timeout: 12)
        guard code == 0, let img = NSImage(contentsOfFile: tmp) else {
            DispatchQueue.main.async {
                self.qrHint.stringValue = "尚未生成二维码。点击「重启 NapCat」后等待 10 秒再刷新。"
            }
            return
        }
        DispatchQueue.main.async {
            self.qrImage.image = img
            let attrs = try? FileManager.default.attributesOfItem(atPath: tmp)
            if let d = attrs?[.modificationDate] as? Date {
                let f = DateFormatter()
                f.dateFormat = "HH:mm:ss"
                self.qrHint.stringValue = "二维码生成于 \(f.string(from: d))，有效期约 2 分钟；扫码成功后自动变绿。"
            }
        }
    }

    private func regenQR() {
        qrHint.stringValue = "正在重启 NapCat 生成新二维码…"
        restartNapCat()
    }

    private func restartNapCat() {
        let cfg = AppConfig.shared.cfg
        qqStatus.stringValue = "♻️ 正在重启 NapCat 容器…"
        DispatchQueue.global(qos: .userInitiated).async {
            _ = Shell.run(cfg.dockerPath, ["restart", cfg.napcatContainer], timeout: 90)
            Thread.sleep(forTimeInterval: 8)
            DispatchQueue.main.async { self.refreshQQ() }
        }
    }

    @objc private func buttonClicked(_ sender: NSButton) {
        sender.onAction?()
    }
}

private var actionKey: UInt8 = 0
extension NSButton {
    var onAction: (() -> Void)? {
        get { objc_getAssociatedObject(self, &actionKey) as? () -> Void }
        set { objc_setAssociatedObject(self, &actionKey, newValue, .OBJC_ASSOCIATION_RETAIN_NONATOMIC) }
    }
}
