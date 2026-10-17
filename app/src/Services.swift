import Foundation
import AppKit

enum ServiceState: Equatable {
    case unknown
    case stopped
    case starting
    case runningManaged
    case runningExternal
    case failed(String)

    var label: String {
        switch self {
        case .unknown: return "未知"
        case .stopped: return "未运行"
        case .starting: return "启动中"
        case .runningManaged: return "运行中（APP 托管）"
        case .runningExternal: return "运行中（后台 launchd）"
        case .failed(let why): return "失败：\(why)"
        }
    }

    var dot: String {
        switch self {
        case .unknown: return "⚪"
        case .stopped: return "⚫"
        case .starting: return "🟡"
        case .runningManaged, .runningExternal: return "🟢"
        case .failed: return "🔴"
        }
    }

    var isUp: Bool {
        switch self {
        case .runningManaged, .runningExternal: return true
        default: return false
        }
    }
}

enum ServiceKind {
    case httpPort(Int)          // 端口探活
    case pythonScript(String)   // pgrep 脚本名
    case dockerContainer(String)
}

final class Service {
    let id: String
    let title: String
    let kind: ServiceKind
    let launchPath: String
    let arguments: [String]
    let workdir: String?
    var process: Process?
    var state: ServiceState = .unknown
    var lastStart: Date?
    var restartCount: Int = 0
    var logHandle: FileHandle?

    init(id: String, title: String, kind: ServiceKind, launchPath: String, arguments: [String], workdir: String? = nil) {
        self.id = id
        self.title = title
        self.kind = kind
        self.launchPath = launchPath
        self.arguments = arguments
        self.workdir = workdir
    }

    /// 展示用状态文案（容器 / launchd 分开说清楚）
    var stateLabel: String {
        if case .runningExternal = state {
            if case .dockerContainer = kind { return "运行中（容器）" }
            return "运行中（launchd 托管）"
        }
        return state.label
    }

    /// 进程 PID：APP 托管时取自身进程，launchd 托管时按脚本名反查
    var pid: Int32 {
        if let p = process, p.isRunning { return p.processIdentifier }
        if case .pythonScript(let name) = kind {
            return Shell.pgrep(name).first ?? 0
        }
        return 0
    }

    /// 由 APP 托管时写入的日志文件
    var logURL: URL {
        AppConfig.shared.logsDir.appendingPathComponent("\(id).app.log")
    }
}

final class ServiceSupervisor {
    static let shared = ServiceSupervisor()

    private(set) var services: [Service] = []
    private var timer: Timer?
    private var qqTimer: Timer?
    private var startedByApp = Set<String>()
    private(set) var supervising = true
    private(set) var qqOnline = false
    var onChange: (() -> Void)?

    private init() {}

    func build() {
        let cfg = AppConfig.shared.cfg
        let py = cfg.pythonPath

        services = [
            Service(id: "backend", title: "后端服务 :\(cfg.port)",
                    kind: .httpPort(cfg.port),
                    launchPath: py,
                    arguments: ["-m", "uvicorn", "app:app", "--host", "127.0.0.1",
                                "--port", "\(cfg.port)", "--log-level", "warning"],
                    workdir: cfg.backendRoot),
            Service(id: "wechat-watch", title: "微信采集",
                    kind: .pythonScript("wechat_watch.py"),
                    launchPath: py, arguments: ["wechat_watch.py"], workdir: cfg.backendRoot),
            Service(id: "web-watch", title: "网页采集（AI福利/白嫖）",
                    kind: .pythonScript("web_watch.py"),
                    launchPath: py, arguments: ["web_watch.py"], workdir: cfg.backendRoot),
            Service(id: "intel-watch", title: "情报简报+事件检测",
                    kind: .pythonScript("intel_watch.py"),
                    launchPath: py, arguments: ["intel_watch.py"], workdir: cfg.backendRoot),
        ]

        if cfg.manageNapCat {
            services.append(Service(id: "napcat", title: "QQ 协议端（NapCat 容器）",
                                    kind: .dockerContainer(cfg.napcatContainer),
                                    launchPath: cfg.dockerPath,
                                    arguments: ["start", cfg.napcatContainer],
                                    workdir: nil))
        }
    }

    func startSupervision() {
        build()
        refresh()
        ensureAllRunning()
        startQQProbe()
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            self?.tick()
        }
        RunLoop.main.add(timer!, forMode: .common)
    }

    // MARK: - 总开关 / QQ 在线状态

    func setSupervising(_ value: Bool) {
        supervising = value
        onChange?()
    }

    func stopAll() {
        setSupervising(false)
        for s in services { stop(s) }
    }

    private func startQQProbe() {
        qqTimer?.invalidate()
        qqTimer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in
            self?.probeQQ()
        }
        RunLoop.main.add(qqTimer!, forMode: .common)
        probeQQ()
    }

    func probeQQ() {
        DispatchQueue.global(qos: .utility).async { [weak self] in
            let url = AppConfig.shared.oneBotURL.appendingPathComponent("get_login_info")
            let obj = Net.getJSON(url, timeout: 4) as? [String: Any]
            let ret = (obj?["retcode"] as? Int) ?? -1
            let uid = ((obj?["data"] as? [String: Any])?["user_id"] as? NSNumber)?.int64Value ?? 0
            let online = (ret == 0 && uid > 0)
            DispatchQueue.main.async {
                guard let self = self else { return }
                if self.qqOnline != online {
                    self.qqOnline = online
                    self.onChange?()
                }
            }
        }
    }

    // MARK: - Docker / OrbStack 保障

    private var lastDockerAttempt: Date = .distantPast
    private var dockerStarting = false

    /// 确保 Docker 引擎可用：OrbStack 没运行就 `open -a OrbStack` 并等待 socket 就绪。
    /// 节流：60 秒内最多尝试一次，避免每 5 秒巡检刷屏。
    @discardableResult
    func ensureDocker(timeout: TimeInterval = 45) -> Bool {
        let (ping, _) = Shell.run(AppConfig.shared.cfg.dockerPath, ["version", "--format", "{{.Server.Version}}"], timeout: 8)
        if ping == 0 { return true }
        if dockerStarting { return true }
        guard Date().timeIntervalSince(lastDockerAttempt) > 60 else { return false }
        lastDockerAttempt = Date()
        dockerStarting = true
        defer { dockerStarting = false }

        let (pg, _) = Shell.run("/usr/bin/pgrep", ["-x", "OrbStack"], timeout: 5)
        if pg != 0 {
            _ = Shell.run("/usr/bin/open", ["-a", "OrbStack"], timeout: 10)
        }
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            Thread.sleep(forTimeInterval: 1.5)
            let (code, _) = Shell.run(AppConfig.shared.cfg.dockerPath, ["version", "--format", "{{.Server.Version}}"], timeout: 6)
            if code == 0 { return true }
        }
        return false
    }

    // MARK: - 状态探测

    func probe(_ s: Service) -> ServiceState {
        if let p = s.process, p.isRunning { return .runningManaged }
        switch s.kind {
        case .httpPort(let port):
            let open = Net.portOpen(host: "127.0.0.1", port: port)
            if open { return startedByApp.contains(s.id) ? .runningManaged : .runningExternal }
            return s.state == .starting ? .starting : .stopped
        case .pythonScript(let name):
            let pids = Shell.pgrep(name).filter { $0 != Int32(ProcessInfo.processInfo.processIdentifier) }
            if !pids.isEmpty { return startedByApp.contains(s.id) ? .runningManaged : .runningExternal }
            return .stopped
        case .dockerContainer(let name):
            let (code, out) = Shell.run(AppConfig.shared.cfg.dockerPath,
                                        ["inspect", "-f", "{{.State.Running}}", name], timeout: 10)
            if code == 0 && out.trimmingCharacters(in: .whitespacesAndNewlines) == "true" {
                return .runningExternal
            }
            if code != 0 {
                // Docker 引擎没起来（开机后 OrbStack 未启动）→ 拉起后重试
                if ensureDocker() { return .starting }
            }
            return .stopped
        }
    }

    func refresh() {
        for s in services {
            let st = probe(s)
            if case .failed = s.state {
                // 保留失败信息，除非确实恢复了
                if st.isUp { s.state = st }
            } else {
                s.state = st
            }
        }
        onChange?()
    }

    private func tick() {
        refresh()
        guard supervising else { return }
        for s in services where !s.state.isUp {
            if case .starting = s.state { continue }
            start(s)
        }
    }

    func ensureAllRunning() {
        for s in services where !probe(s).isUp {
            start(s)
        }
    }

    // MARK: - 启停

    /// 服务 → 登录项 label（后端在登录项里叫 egg-harvester，其余同名）
    private func launchdLabel(_ id: String) -> String? {
        switch id {
        case "backend":        return "website.lanche.egg-harvester"
        case "wechat-watch":   return "website.lanche.wechat-watch"
        case "web-watch":      return "website.lanche.web-watch"
        case "intel-watch":    return "website.lanche.intel-watch"
        default:               return nil
        }
    }

    /// 是否存在对应的登录项（launchd 作业）——存在就交给 launchd 托管，APP 不重复起进程。
    private func launchdJobExists(_ label: String) -> Bool {
        let (code, out) = Shell.run("/bin/launchctl", ["list"], timeout: 8)
        return code == 0 && out.contains(label)
    }

    private func kickstartLaunchd(_ label: String) -> Bool {
        let uid = getuid()
        let (code, _) = Shell.run("/bin/launchctl", ["kickstart", "-k", "gui/\(uid)/\(label)"], timeout: 15)
        return code == 0
    }

    func start(_ s: Service) {
        if probe(s).isUp { refresh(); return }
        if case .dockerContainer = s.kind {
            if !ensureDocker() {
                s.state = .failed("Docker 未就绪（OrbStack 启动失败）")
                onChange?()
                return
            }
        }
        s.state = .starting
        onChange?()

        // launchd 托管的服务：只 kickstart，绝不自己再起一份（否则两个进程抢 8890）
        if let label = launchdLabel(s.id), launchdJobExists(label) {
            _ = kickstartLaunchd(label)
            DispatchQueue.main.asyncAfter(deadline: .now() + 6) { [weak self] in
                let st = self?.probe(s) ?? .unknown
                s.state = st.isUp ? st : .starting
                self?.onChange?()
            }
            return
        }

        let p = Process()
        p.executableURL = URL(fileURLWithPath: s.launchPath)
        p.arguments = s.arguments
        if let wd = s.workdir { p.currentDirectoryURL = URL(fileURLWithPath: wd) }

        var env = ProcessInfo.processInfo.environment
        let basePath = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        env["PATH"] = basePath + ":" + (env["PATH"] ?? "")
        env["PYTHONUNBUFFERED"] = "1"
        p.environment = env

        // 日志
        let logsDir = AppConfig.shared.logsDir
        try? FileManager.default.createDirectory(at: logsDir, withIntermediateDirectories: true)
        let logURL = logsDir.appendingPathComponent("\(s.id).app.log")

        if !FileManager.default.fileExists(atPath: logURL.path) {
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
        } else if let size = try? FileManager.default.attributesOfItem(atPath: logURL.path)[.size] as? NSNumber,
                  size.intValue > 5 * 1024 * 1024 {
            // 简单截断，避免日志无限增长
            try? "".write(to: logURL, atomically: true, encoding: .utf8)
        }

        let handle = (try? FileHandle(forWritingTo: logURL)) ?? FileHandle.standardOutput
        _ = try? handle.seekToEnd()
        handle.write("\n===== [\(Date())] 启动 \(s.title) =====\n".data(using: .utf8)!)
        p.standardOutput = handle
        p.standardError = handle
        s.logHandle = handle

        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async {
                s.process = nil
                s.state = .stopped
                if proc.terminationStatus != 0 {
                    s.state = .failed("exit \(proc.terminationStatus)")
                }
                self?.onChange?()
            }
        }

        do {
            try p.run()
            s.process = p
            s.lastStart = Date()
            s.restartCount += 1
            startedByApp.insert(s.id)
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { [weak self] in
                let st = self?.probe(s) ?? .unknown
                s.state = st.isUp ? st : (s.process != nil ? .starting : st)
                self?.onChange?()
            }
        } catch {
            s.state = .failed(error.localizedDescription)
            onChange?()
        }
    }

    func stop(_ s: Service) {
        switch s.kind {
        case .dockerContainer(let name):
            _ = Shell.run(AppConfig.shared.cfg.dockerPath, ["stop", name], timeout: 60)
        default:
            if let p = s.process, p.isRunning { p.terminate() }
            // 外部（launchd 起来的）也一并结束，交给 launchd 自行重启
            if let pattern = scriptPattern(s) {
                for pid in Shell.pgrep(pattern) where pid != Int32(ProcessInfo.processInfo.processIdentifier) {
                    if pid != Int32(getpid()) { kill(pid, SIGTERM) }
                }
            }
        }
        startedByApp.remove(s.id)
        s.process = nil
        s.state = .stopped
        onChange?()
    }

    func restart(_ s: Service) {
        stop(s)
        DispatchQueue.global().asyncAfter(deadline: .now() + 1.0) {
            DispatchQueue.main.async { self.start(s) }
        }
    }

    func restartAll() {
        for s in services { stop(s) }
        DispatchQueue.global().asyncAfter(deadline: .now() + 1.5) {
            DispatchQueue.main.async { self.ensureAllRunning() }
        }
    }

    private func scriptPattern(_ s: Service) -> String? {
        if case .pythonScript(let name) = s.kind { return name }
        if s.arguments.contains(where: { $0.hasSuffix(".py") }) {
            return s.arguments.first { $0.hasSuffix(".py") }
        }
        return nil
    }

    /// APP 退出时停掉自己拉起的进程；不碰 launchd 托管的
    func shutdownOwned() {
        timer?.invalidate()
        for s in services where startedByApp.contains(s.id) {
            if let p = s.process, p.isRunning { p.terminate() }
            if case .pythonScript = s.kind, let pattern = scriptPattern(s) {
                for pid in Shell.pgrep(pattern) where pid != Int32(getpid()) { kill(pid, SIGTERM) }
            }
        }
    }

    var allUp: Bool { services.allSatisfy { $0.state.isUp } }
    var statusSummary: String {
        let up = services.filter { $0.state.isUp }.count
        return "\(up)/\(services.count) 服务在线"
    }
}
