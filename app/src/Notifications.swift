import Foundation
import UserNotifications
import AppKit

/// 重要事件原生通知桥：直接轮询后端 /important_events，用 macOS 通知中心弹出。
final class NotifierBridge {
    static let shared = NotifierBridge()

    private var timer: Timer?
    private var busy = false
    private var authorized = false

    private init() {}

    func start() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { granted, err in
            self.authorized = granted
            if let err = err {
                // 只记一次，避免每次启动刷屏；ad-hoc 签名下系统通知常年不可用
                if !UserDefaults.standard.bool(forKey: "notifyErrLogged") {
                    NSLog("[Notifier] 系统通知不可用，已自动降级为应用内提醒卡片：\(err.localizedDescription)")
                    UserDefaults.standard.set(true, forKey: "notifyErrLogged")
                }
            }
        }
        UNUserNotificationCenter.current().delegate = NotificationDelegate.shared

        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            self?.poll()
        }
        RunLoop.main.add(timer!, forMode: .common)
        DispatchQueue.main.asyncAfter(deadline: .now() + 6) { self.poll() }
    }

    func poll() {
        guard !busy else { return }
        busy = true
        DispatchQueue.global(qos: .utility).async {
            defer { self.busy = false }
            guard let url = URL(string: "http://127.0.0.1:\(AppConfig.shared.cfg.port)/important_events?unnotified_only=true&limit=20") else { return }
            guard let obj = Net.getJSON(url) as? [String: Any],
                  let events = obj["events"] as? [[String: Any]], !events.isEmpty else { return }

            let primed = UserDefaults.standard.bool(forKey: "notifyPrimed")
            if !primed {
                // 首次运行：把历史事件标记为已通知，避免开机轰炸
                for e in events { self.markNotified(e) }
                UserDefaults.standard.set(true, forKey: "notifyPrimed")
                return
            }

            for e in events.prefix(5) {
                let title = (e["title"] as? String) ?? "重要情报"
                let body = String(((e["summary"] as? String) ?? "").prefix(180))
                let type = (e["event_type"] as? String) ?? "info"
                if self.authorized {
                    self.deliver(title: "\(Self.emoji(type)) \(title)", body: body, id: "\(e["id"] ?? 0)")
                } else {
                    // 无系统通知权限时，用应用内卡片提醒（右上角浮层）
                    DispatchQueue.main.async {
                        let eid = "\(e["id"] ?? 0)"
                        ToastCenter.shared.show(emoji: Self.emoji(type), title: title, body: body) {
                            AppState.shared.openInMainWindow(Self.eventURL(eid))
                        }
                    }
                }
                self.markNotified(e)
            }

            // 更新授权状态（用户可能在系统设置里改过）
            UNUserNotificationCenter.current().getNotificationSettings { settings in
                self.authorized = (settings.authorizationStatus == .authorized
                                   || settings.authorizationStatus == .provisional)
            }
        }
    }

    static func intelURL() -> URL {
        URL(string: "http://127.0.0.1:\(AppConfig.shared.cfg.port)/#intel")!
    }

    /// 事件深链：打开主窗口 → 切到简报页 → 弹出该事件的原始消息上下文
    static func eventURL(_ id: String) -> URL {
        URL(string: "http://127.0.0.1:\(AppConfig.shared.cfg.port)/?event=\(id)")!
    }

    /// 手动预览提醒卡片
    func testToast() {
        ToastCenter.shared.show(emoji: "🎁", title: "重要情报示例：新站注册送额度",
                                body: "该站提供 gpt-5.6-luna，注册即送 20 美元额度，支持每日签到。") {
            AppState.shared.openInMainWindow(NotifierBridge.intelURL())
        }
    }

    static func emoji(_ type: String) -> String {
        switch type {
        case "key": return "🔑"
        case "account": return "👤"
        case "opportunity": return "🎁"
        case "urgent": return "🚨"
        case "announcement": return "📢"
        default: return "🔔"
        }
    }

    private func deliver(title: String, body: String, id: String) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        content.userInfo = ["url": "http://127.0.0.1:\(AppConfig.shared.cfg.port)/?event=\(id)"]
        let req = UNNotificationRequest(identifier: "event-\(id)-\(UUID().uuidString.prefix(6))",
                                        content: content, trigger: nil)
        UNUserNotificationCenter.current().add(req)
    }

    private func markNotified(_ e: [String: Any]) {
        guard let eid = e["id"] as? Int ?? (e["id"] as? NSNumber)?.intValue else { return }
        guard let url = URL(string: "http://127.0.0.1:\(AppConfig.shared.cfg.port)/important_events/notify") else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        req.httpBody = "event_id=\(eid)".data(using: .utf8)
        req.timeoutInterval = 8
        let sem = DispatchSemaphore(value: 0)
        URLSession.shared.dataTask(with: req) { _, _, _ in sem.signal() }.resume()
        _ = sem.wait(timeout: .now() + 8)
    }
}

final class NotificationDelegate: NSObject, UNUserNotificationCenterDelegate {
    static let shared = NotificationDelegate()

    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        if let s = response.notification.request.content.userInfo["url"] as? String,
           let u = URL(string: s) {
            AppState.shared.openInMainWindow(u)
        }
        completionHandler()
    }
}

/// 全局状态：主窗口引用，供通知点击回调使用
final class AppState {
    static let shared = AppState()
    weak var mainWindowController: MainWindowController?
    func openInMainWindow(_ url: URL) {
        DispatchQueue.main.async {
            guard let c = self.mainWindowController else { return }
            c.show()
            let comps = URLComponents(url: url, resolvingAgainstBaseURL: false)
            let eventId = comps?.queryItems?.first(where: { $0.name == "event" })?.value.flatMap(Int.init)
            let panel = url.fragment ?? ""
            if let id = eventId {
                // 事件深链：页面已就绪就直接执行；否则整页加载（页面 onload 的 handleDeepLink 会再处理 ?event=）
                // 之前只 blind evaluate：App 冷启动时页面还没加载，evaluate 落空，点击就"没有反应"。
                c.evaluateResult("typeof openEventById === 'function' ? 'ok' : 'missing'") { v in
                    if (v as? String) == "ok" {
                        c.evaluate("if (typeof openEventById === 'function') openEventById(\(id));")
                    } else {
                        c.show(url: url)
                    }
                }
            } else if !panel.isEmpty {
                c.evaluateResult("typeof switchView === 'function' ? 'ok' : 'missing'") { v in
                    if (v as? String) == "ok" {
                        c.evaluate("if (typeof switchView === 'function') switchView('\(panel)');")
                    } else {
                        c.show(url: url)
                    }
                }
            } else {
                c.show(url: url)
            }
        }
    }
}
