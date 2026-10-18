import AppKit

/// 应用内提醒卡片（右上角浮层）——不依赖系统通知权限，永远可用
final class ToastCenter {
    static let shared = ToastCenter()

    private var windows: [ToastWindow] = []
    private let cardWidth: CGFloat = 320

    private init() {}

    func show(emoji: String, title: String, body: String, onClick: (() -> Void)? = nil) {
        let toast = ToastWindow(emoji: emoji, title: title, body: body,
                                width: cardWidth,
                                onClick: { [weak self] in
                                    onClick?()
                                    self?.arrange(animated: true)
                                },
                                onClose: { [weak self] win in
                                    self?.dismiss(win)
                                })
        windows.append(toast)
        arrange(animated: false)
        toast.orderFrontRegardless()

    }

    private func dismiss(_ toast: ToastWindow) {
        windows.removeAll { $0 === toast }
        toast.fadeOutAndClose()
        arrange(animated: true)
    }

    private func arrange(animated: Bool) {
        guard let screen = NSScreen.main else { return }
        let vf = screen.visibleFrame
        var y = vf.maxY - 12
        for w in windows {
            let h = w.frame.height
            y -= h
            let target = NSRect(x: vf.maxX - cardWidth - 16, y: y, width: cardWidth, height: h)
            if animated {
                NSAnimationContext.runAnimationGroup { ctx in
                    ctx.duration = 0.22
                    w.animator().setFrame(target, display: true)
                }
            } else {
                w.setFrame(target, display: true)
            }
            y -= 10
        }
    }

    /// 自检：把最上面一张卡片渲染成 PNG（用于无屏幕录制权限时验证外观）
    func renderTop(to path: String) -> Bool {
        guard let w = windows.last else { return false }
        return w.renderPNG(to: path)
    }

    func dismissAll() {
        for w in windows { w.fadeOutAndClose() }
        windows.removeAll()
    }
}

final class ToastWindow: NSWindow {
    private let onClick: () -> Void
    private let onClose: (ToastWindow) -> Void
    private var dismissTimer: Timer?
    private var remaining: TimeInterval = 18

    init(emoji: String, title: String, body: String, width: CGFloat,
         onClick: @escaping () -> Void, onClose: @escaping (ToastWindow) -> Void) {
        self.onClick = onClick
        self.onClose = onClose

        // 纯色卡片：之前用 NSVisualEffectView 会在圆角外露出一圈半透明方块。
        // 用自定义 ToastCardView 而不是 NSView：普通视图上挂 NSClickGestureRecognizer
        // 点中文字标签时手势不触发（就是"点击查看详情没反应"的根因）。
        let bg = ToastCardView()
        bg.wantsLayer = true
        bg.layer?.backgroundColor = NSColor.controlBackgroundColor.cgColor
        bg.layer?.cornerRadius = 12
        bg.layer?.masksToBounds = true
        bg.layer?.borderWidth = 1
        bg.layer?.borderColor = NSColor.separatorColor.withAlphaComponent(0.7).cgColor

        let emojiLabel = NSTextField(labelWithString: emoji)
        emojiLabel.font = .systemFont(ofSize: 18)
        emojiLabel.translatesAutoresizingMaskIntoConstraints = false

        let titleLabel = NSTextField(labelWithString: title)
        titleLabel.font = .systemFont(ofSize: 12.5, weight: .semibold)
        titleLabel.alignment = .left
        (titleLabel.cell as? NSTextFieldCell)?.alignment = .left
        (titleLabel.cell as? NSTextFieldCell)?.lineBreakMode = .byTruncatingTail
        titleLabel.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        titleLabel.textColor = .labelColor
        titleLabel.lineBreakMode = .byTruncatingTail
        titleLabel.translatesAutoresizingMaskIntoConstraints = false

        let bodyLabel = NSTextField(wrappingLabelWithString: body)
        bodyLabel.font = .systemFont(ofSize: 11.5)
        bodyLabel.textColor = .secondaryLabelColor
        bodyLabel.alignment = .left
        (bodyLabel.cell as? NSTextFieldCell)?.alignment = .left
        bodyLabel.maximumNumberOfLines = 3
        bodyLabel.preferredMaxLayoutWidth = width - 28
        bodyLabel.translatesAutoresizingMaskIntoConstraints = false

        let closeBtn = NSButton(title: "✕", target: nil, action: nil)
        closeBtn.isBordered = false
        closeBtn.font = .systemFont(ofSize: 11)
        closeBtn.contentTintColor = .secondaryLabelColor
        closeBtn.translatesAutoresizingMaskIntoConstraints = false

        // 自动关闭进度条：18 秒，鼠标停留时暂停。
        let progress = NSView()
        progress.wantsLayer = true
        progress.layer?.backgroundColor = NSColor.controlAccentColor.withAlphaComponent(0.65).cgColor
        progress.translatesAutoresizingMaskIntoConstraints = false

        let hint = NSTextField(labelWithString: "点击查看详情")
        hint.font = .systemFont(ofSize: 10)
        hint.textColor = .tertiaryLabelColor
        hint.translatesAutoresizingMaskIntoConstraints = false

        bg.addSubview(emojiLabel)
        bg.addSubview(titleLabel)
        bg.addSubview(bodyLabel)
        bg.addSubview(closeBtn)
        bg.addSubview(hint)
        bg.addSubview(progress)

        NSLayoutConstraint.activate([
            emojiLabel.leadingAnchor.constraint(equalTo: bg.leadingAnchor, constant: 14),
            emojiLabel.topAnchor.constraint(equalTo: bg.topAnchor, constant: 12),
            emojiLabel.widthAnchor.constraint(equalToConstant: 34),
            emojiLabel.heightAnchor.constraint(equalToConstant: 22),

            titleLabel.leadingAnchor.constraint(equalTo: emojiLabel.trailingAnchor, constant: 10),
            titleLabel.trailingAnchor.constraint(equalTo: closeBtn.leadingAnchor, constant: -8),
            titleLabel.centerYAnchor.constraint(equalTo: emojiLabel.centerYAnchor),

            closeBtn.trailingAnchor.constraint(equalTo: bg.trailingAnchor, constant: -10),
            closeBtn.centerYAnchor.constraint(equalTo: titleLabel.centerYAnchor),
            closeBtn.widthAnchor.constraint(equalToConstant: 18),

            bodyLabel.leadingAnchor.constraint(equalTo: bg.leadingAnchor, constant: 14),
            bodyLabel.trailingAnchor.constraint(equalTo: bg.trailingAnchor, constant: -14),
            bodyLabel.topAnchor.constraint(equalTo: emojiLabel.bottomAnchor, constant: 6),
            bodyLabel.bottomAnchor.constraint(equalTo: hint.topAnchor, constant: -6),

            hint.leadingAnchor.constraint(equalTo: bg.leadingAnchor, constant: 14),
            hint.bottomAnchor.constraint(equalTo: bg.bottomAnchor, constant: -8),

            progress.leadingAnchor.constraint(equalTo: bg.leadingAnchor),
            progress.bottomAnchor.constraint(equalTo: bg.bottomAnchor),
            progress.heightAnchor.constraint(equalToConstant: 2)
        ])

        let bodyFont = NSFont.systemFont(ofSize: 12)
        let textRect = (body as NSString).boundingRect(
            with: NSSize(width: width - 28, height: 400),
            options: [.usesLineFragmentOrigin, .usesFontLeading],
            attributes: [.font: bodyFont])
        let textH = min(ceil(textRect.height), 58)
        let height = ceil(10 + 20 + 5 + textH + 4 + 12 + 9)

        super.init(contentRect: NSRect(x: 0, y: 0, width: width, height: height),
                   styleMask: [.borderless],
                   backing: .buffered, defer: false)
        // 关键：close() 后由 ARC 释放，避免 AppKit 再 release 一次导致 objc_release 崩溃
        self.isReleasedWhenClosed = false
        self.isOpaque = false
        self.backgroundColor = .clear
        self.hasShadow = true
        self.level = .floating
        self.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        self.ignoresMouseEvents = false
        self.isMovableByWindowBackground = true
        self.contentView = bg

        // 点击卡片任意位置 → 查看详情（✕ 按钮除外）
        bg.onClick = { [weak self] in
            guard let self = self else { return }
            self.onClick()
            self.onClose(self)
        }
        bg.closeButton = closeBtn

        closeBtn.target = self
        closeBtn.action = #selector(closeClicked)

        let widthConstraint = progress.widthAnchor.constraint(equalToConstant: width)
        widthConstraint.isActive = true
        dismissTimer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] timer in
            guard let self = self else { timer.invalidate(); return }
            guard !self.frame.contains(NSEvent.mouseLocation) else { return }
            self.remaining -= 0.1
            widthConstraint.constant = width * CGFloat(max(0, self.remaining) / 18)
            if self.remaining <= 0 { self.onClose(self) }
        }
    }

    @objc private func closeClicked() {
        onClose(self)
    }

    func renderPNG(to path: String) -> Bool {
        guard let view = contentView,
              let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return false }

        view.cacheDisplay(in: view.bounds, to: rep)
        guard let png = rep.representation(using: .png, properties: [:]) else { return false }
        return (try? png.write(to: URL(fileURLWithPath: path))) != nil
    }

    func fadeOutAndClose() {
        dismissTimer?.invalidate()
        dismissTimer = nil
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = 0.18
            self.animator().alphaValue = 0
        } completionHandler: { [weak self] in
            self?.orderOut(nil)
            self?.close()
        }
    }

    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

/// 卡片容器：把除 ✕ 按钮外的所有点击都导向"查看详情"。
/// 不依赖手势识别器（点中 NSTextField 标签时手势不会触发），而是直接接管 hitTest。
final class ToastCardView: NSView {
    var onClick: (() -> Void)?
    weak var closeButton: NSButton?

    private var downPoint: NSPoint = .zero
    private var downOnCloseButton = false

    override func hitTest(_ point: NSPoint) -> NSView? {
        // ✕ 按钮区域保持可点，其余区域整卡响应
        if let btn = closeButton {
            // frame 的坐标空间依赖 superview；显式转换后再判断，避免
            // Auto Layout 完成后某些窗口尺寸下叉号误落到整卡点击逻辑。
            let closeRect = btn.convert(btn.bounds, to: self).insetBy(dx: -4, dy: -4)
            if closeRect.contains(point) { return btn }
        }
        return self
    }

    override func mouseDown(with event: NSEvent) {
        downPoint = convert(event.locationInWindow, from: nil)
        downOnCloseButton = closeButton.map {
            $0.convert($0.bounds, to: self).insetBy(dx: -4, dy: -4).contains(downPoint)
        } ?? false
    }

    // 支持按住拖动挪卡片，抬起时位移小于 4pt 才算"点击"
    override var mouseDownCanMoveWindow: Bool { true }

    override func mouseUp(with event: NSEvent) {
        let up = convert(event.locationInWindow, from: nil)
        let upOnCloseButton = closeButton.map {
            $0.convert($0.bounds, to: self).insetBy(dx: -4, dy: -4).contains(up)
        } ?? false
        // 叉号只负责关闭通知，绝不能冒泡成“查看详情”并唤起主窗口。
        if downOnCloseButton || upOnCloseButton { return }
        if abs(up.x - downPoint.x) < 4, abs(up.y - downPoint.y) < 4 {
            onClick?()
        }
        downOnCloseButton = false
    }
}
