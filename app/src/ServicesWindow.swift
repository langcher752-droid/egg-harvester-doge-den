import AppKit

/// 服务状态窗口：每个采集/后端组件一行，可单独启停重启，并查看日志尾巴
final class ServicesWindowController: NSWindowController, NSWindowDelegate {
    private var rowsStack: NSStackView!
    private var logView: NSTextView!
    private var logSelector: NSPopUpButton!
    private var summaryLabel: NSTextField!
    private var timer: Timer?

    convenience init() {
        let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 760, height: 620),
                           styleMask: [.titled, .closable, .resizable, .miniaturizable],
                           backing: .buffered, defer: false)
        win.title = "服务状态"
        win.isReleasedWhenClosed = false
        win.center()
        self.init(window: win)
        win.delegate = self
        buildUI()
        reload()
        timer = Timer.scheduledTimer(withTimeInterval: 4, repeats: true) { [weak self] _ in self?.reload() }
        RunLoop.main.add(timer!, forMode: .common)
    }

    deinit { timer?.invalidate() }

    override func showWindow(_ sender: Any?) {
        super.showWindow(sender)
        window?.makeKeyAndOrderFront(nil)
        reload()
    }

    // MARK: - UI

    private func buildUI() {
        guard let content = window?.contentView else { return }

        summaryLabel = NSTextField(labelWithString: "")
        summaryLabel.font = .systemFont(ofSize: 13, weight: .medium)
        summaryLabel.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(summaryLabel)

        let buttons = NSStackView()
        buttons.orientation = .horizontal
        buttons.spacing = 8
        buttons.translatesAutoresizingMaskIntoConstraints = false
        buttons.addArrangedSubview(simpleButton("▶️ 全部启动") { ServiceSupervisor.shared.setSupervising(true); ServiceSupervisor.shared.ensureAllRunning() })
        buttons.addArrangedSubview(simpleButton("🔁 全部重启") { ServiceSupervisor.shared.restartAll() })
        buttons.addArrangedSubview(simpleButton("⏹ 全部停止") { ServiceSupervisor.shared.stopAll() })
        content.addSubview(buttons)

        rowsStack = NSStackView()
        rowsStack.orientation = .vertical
        rowsStack.alignment = .leading
        rowsStack.spacing = 6
        rowsStack.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(rowsStack)

        let logBox = NSScrollView()
        logBox.translatesAutoresizingMaskIntoConstraints = false
        logBox.hasVerticalScroller = true
        logBox.borderType = .bezelBorder
        logBox.autohidesScrollers = true
        logView = NSTextView(frame: .zero)
        logView.isVerticallyResizable = true
        logView.autoresizingMask = [.width]
        logView.textContainer?.widthTracksTextView = true
        logView.isEditable = false
        logView.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
        logView.textColor = .labelColor
        logView.backgroundColor = .textBackgroundColor
        logBox.documentView = logView
        content.addSubview(logBox)

        logSelector = NSPopUpButton(frame: .zero, pullsDown: false)
        logSelector.translatesAutoresizingMaskIntoConstraints = false
        for s in ServiceSupervisor.shared.services { logSelector.addItem(withTitle: s.title) }
        logSelector.target = self
        logSelector.action = #selector(logSelectionChanged)
        content.addSubview(logSelector)

        let logButtons = NSStackView()
        logButtons.orientation = .horizontal
        logButtons.spacing = 8
        logButtons.translatesAutoresizingMaskIntoConstraints = false
        logButtons.addArrangedSubview(simpleButton("🔄 刷新日志") { [weak self] in self?.loadLog() })
        logButtons.addArrangedSubview(simpleButton("📂 在 Finder 中显示") {
            NSWorkspace.shared.open(AppConfig.shared.logsDir)
        })
        content.addSubview(logButtons)

        NSLayoutConstraint.activate([
            summaryLabel.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 20),
            summaryLabel.topAnchor.constraint(equalTo: content.topAnchor, constant: 16),

            buttons.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 20),
            buttons.centerYAnchor.constraint(equalTo: summaryLabel.centerYAnchor),

            rowsStack.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 20),
            rowsStack.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -20),
            rowsStack.topAnchor.constraint(equalTo: summaryLabel.bottomAnchor, constant: 14),

            logSelector.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 20),
            logSelector.topAnchor.constraint(equalTo: rowsStack.bottomAnchor, constant: 16),
            logSelector.widthAnchor.constraint(equalToConstant: 260),

            logButtons.leadingAnchor.constraint(equalTo: logSelector.trailingAnchor, constant: 10),
            logButtons.centerYAnchor.constraint(equalTo: logSelector.centerYAnchor),

            logBox.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 20),
            logBox.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -20),
            logBox.topAnchor.constraint(equalTo: logSelector.bottomAnchor, constant: 8),
            logBox.bottomAnchor.constraint(equalTo: content.bottomAnchor, constant: -16)
        ])

        loadLog()
    }

    private func simpleButton(_ title: String, action: @escaping () -> Void) -> NSButton {
        let b = NSButton(title: title, target: self, action: #selector(anyButton(_:)))
        b.bezelStyle = .rounded
        b.onAction = action
        return b
    }

    @objc private func anyButton(_ sender: NSButton) { sender.onAction?() }

    @objc private func logSelectionChanged() { loadLog() }

    // MARK: - 刷新

    func reload() {
        guard rowsStack != nil else { return }
        for v in rowsStack.arrangedSubviews { rowsStack.removeArrangedSubview(v); v.removeFromSuperview() }

        let sup = ServiceSupervisor.shared
        let up = sup.services.filter { $0.state.isUp }.count
        summaryLabel.stringValue = "\(up)/\(sup.services.count) 运行中 · NapCat QQ: \(sup.qqOnline ? "已登录" : "未登录")"

        for s in sup.services {
            let row = NSStackView()
            row.orientation = .horizontal
            row.spacing = 10
            row.alignment = .centerY

            let dot = NSTextField(labelWithString: s.state.dot)
            dot.font = .systemFont(ofSize: 13)
            let name = NSTextField(labelWithString: s.title)
            name.font = .systemFont(ofSize: 13, weight: .medium)
            name.translatesAutoresizingMaskIntoConstraints = false
            name.widthAnchor.constraint(equalToConstant: 170).isActive = true
            let state = NSTextField(labelWithString: s.stateLabel + (s.pid > 0 ? " · pid \(s.pid)" : ""))
            state.font = .systemFont(ofSize: 12)
            state.textColor = .secondaryLabelColor
            state.translatesAutoresizingMaskIntoConstraints = false
            state.widthAnchor.constraint(equalToConstant: 150).isActive = true

            row.addArrangedSubview(dot)
            row.addArrangedSubview(name)
            row.addArrangedSubview(state)
            row.addArrangedSubview(simpleButton("启动") { ServiceSupervisor.shared.start(s) })
            row.addArrangedSubview(simpleButton("重启") { ServiceSupervisor.shared.restart(s) })
            row.addArrangedSubview(simpleButton("停止") { ServiceSupervisor.shared.stop(s) })
            let logBtn = simpleButton("日志") { [weak self] in
                if let i = ServiceSupervisor.shared.services.firstIndex(where: { $0.id == s.id }) {
                    self?.logSelector.selectItem(at: i)
                    self?.loadLog()
                }
            }
            row.addArrangedSubview(logBtn)

            rowsStack.addArrangedSubview(row)
        }
    }

    private func loadLog() {
        guard let logSelector = logSelector, logSelector.indexOfSelectedItem >= 0 else { return }
        let idx = logSelector.indexOfSelectedItem
        let services = ServiceSupervisor.shared.services
        guard idx < services.count else { return }
        let svc = services[idx]
        let text: String
        if let data = try? Data(contentsOf: svc.logURL), data.count > 0 {
            let all = String(decoding: data.suffix(24_000), as: UTF8.self)
            text = all
        } else {
            text = "(日志文件为空或不存在：\(svc.logURL.path))"
        }
        logView.string = text
        logView.scrollToEndOfDocument(nil)
    }
}
