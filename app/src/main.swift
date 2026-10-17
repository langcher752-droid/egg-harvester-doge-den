import AppKit

// MARK: - 命令行自检模式：./GroupIntel --check
if CommandLine.arguments.contains("--check") {
    let cfg = AppConfig.shared.cfg
    print("backendRoot : \(cfg.backendRoot)")
    print("exists      : \(FileManager.default.fileExists(atPath: cfg.backendRoot + "/app.py"))")
    print("pythonPath  : \(cfg.pythonPath) (\(FileManager.default.isExecutableFile(atPath: cfg.pythonPath) ? "ok" : "MISSING"))")
    print("port        : \(cfg.port) (\(Net.portOpen(host: "127.0.0.1", port: cfg.port) ? "listening" : "closed"))")
    print("dockerPath  : \(cfg.dockerPath)")
    print("napcat      : \(cfg.napcatContainer) manage=\(cfg.manageNapCat)")
    print("onebot :3000: \(Net.portOpen(host: "127.0.0.1", port: 3000) ? "open" : "closed")")
    print("supportDir  : \(AppConfig.shared.supportDir.path)")
    exit(0)
}

// 单实例：已有同名 APP 在跑就把旧的唤到前台，自己退出（自检模式除外）
let selfTestFlags = ["--check", "--snapshot", "--toast-image", "--account-image", "--services", "--goto", "--js", "--eval", "--wait"]
let isSelfTest = CommandLine.arguments.contains { selfTestFlags.contains($0) }
if !isSelfTest, let bid = Bundle.main.bundleIdentifier {
    let others = NSRunningApplication.runningApplications(withBundleIdentifier: bid)
        .filter { $0.processIdentifier != ProcessInfo.processInfo.processIdentifier }
    if let first = others.first {
        first.activate(options: [.activateAllWindows])
        exit(0)
    }
}

// 命令行服务自检：./GroupIntel --services
if CommandLine.arguments.contains("--services") {
    let sup = ServiceSupervisor.shared
    sup.build()
    sup.refresh()
    for s in sup.services {
        print(String(format: "%-14@ %@ pid=%d log=%@", s.id as NSString, s.state.dot + " " + s.stateLabel, s.pid, s.logURL.lastPathComponent))
    }
    // 同步探测（异步版在 CLI 里来不及返回）
    let info = Net.getJSON(AppConfig.shared.oneBotURL.appendingPathComponent("get_login_info"), timeout: 4) as? [String: Any]
    let ret = (info?["retcode"] as? Int) ?? -1
    let uid = ((info?["data"] as? [String: Any])?["user_id"] as? NSNumber)?.int64Value ?? 0
    let nickname = ((info?["data"] as? [String: Any])?["nickname"] as? String) ?? ""
    print("qqOnline=\(ret == 0 && uid > 0)  uin=\(uid)  nickname=\(nickname)")
    exit(0)
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
