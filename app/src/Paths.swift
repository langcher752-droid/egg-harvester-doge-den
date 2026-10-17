import Foundation

// MARK: - 运行配置（可被 ~/Library/Application Support/GroupIntel/app.json 覆盖）

struct RuntimeConfig: Codable {
    var backendRoot: String
    var pythonPath: String
    var port: Int
    var napcatContainer: String
    var dockerPath: String
    var autoStartApp: Bool
    var manageNapCat: Bool
}

final class AppConfig {
    static let shared = AppConfig()

    let supportDir: URL
    let configURL: URL
    private(set) var cfg: RuntimeConfig

    private init() {
        let home = FileManager.default.homeDirectoryForCurrentUser
        supportDir = home.appendingPathComponent("Library/Application Support/GroupIntel", isDirectory: true)
        try? FileManager.default.createDirectory(at: supportDir, withIntermediateDirectories: true)
        configURL = supportDir.appendingPathComponent("app.json")

        var defaults: [String: Any] = [:]
        if let u = Bundle.main.url(forResource: "defaults", withExtension: "json"),
           let d = try? Data(contentsOf: u),
           let j = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any] {
            defaults = j
        }

        cfg = RuntimeConfig(
            backendRoot: (defaults["backendRoot"] as? String) ?? AppConfig.detectBackendRoot(),
            pythonPath: (defaults["pythonPath"] as? String) ?? AppConfig.detectPython(),
            port: (defaults["port"] as? Int) ?? 8890,
            napcatContainer: (defaults["napcatContainer"] as? String) ?? "napcat",
            dockerPath: (defaults["dockerPath"] as? String) ?? AppConfig.detectDocker(),
            autoStartApp: (defaults["autoStartApp"] as? Bool) ?? true,
            manageNapCat: (defaults["manageNapCat"] as? Bool) ?? true
        )
        if let d = try? Data(contentsOf: configURL),
           let saved = try? JSONDecoder().decode(RuntimeConfig.self, from: d) {
            cfg = saved
        }
        save()
    }

    func save() {
        if let d = try? JSONEncoder().encode(cfg) {
            try? d.write(to: configURL, options: .atomic)
        }
    }

    func update(_ mutate: (inout RuntimeConfig) -> Void) {
        mutate(&cfg)
        save()
    }

    // MARK: 派生路径

    var backendURL: URL { URL(fileURLWithPath: cfg.backendRoot, isDirectory: true) }
    var baseURL: URL { URL(string: "http://127.0.0.1:\(cfg.port)")! }
    var oneBotURL: URL { URL(string: "http://127.0.0.1:3000")! }
    var envURL: URL { backendURL.appendingPathComponent(".env") }
    var logsDir: URL { backendURL.appendingPathComponent("logs", isDirectory: true) }
    var mediaDir: URL { backendURL.appendingPathComponent("media", isDirectory: true) }
    var launchAgentAppURL: URL {
        let home = FileManager.default.homeDirectoryForCurrentUser
        return home.appendingPathComponent("Library/LaunchAgents/website.lanche.groupintel-app.plist")
    }

    // MARK: 探测

    static func detectBackendRoot() -> String {
        // 1) bundle 里附带的 backendRoot.txt（build.sh 写入）
        if let u = Bundle.main.url(forResource: "backendRoot", withExtension: "txt"),
           let s = try? String(contentsOf: u, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines),
           !s.isEmpty, FileManager.default.fileExists(atPath: s + "/app.py") {
            return s
        }
        // 2) 可执行文件回溯（安装后 Resources/backend）
        let exe = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
        let guess = exe.deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("Resources/backend")
        if FileManager.default.fileExists(atPath: guess.appendingPathComponent("app.py").path) {
            return guess.path
        }
        return FileManager.default.currentDirectoryPath
    }

    static func detectPython() -> String {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let candidates = [
            home + "/Applications/GroupIntel.app/Contents/MacOS/python3.13",
            home + "/.workbuddy/binaries/python/versions/3.13.12/bin/python3.13",
            "/opt/homebrew/bin/python3.13",
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/usr/bin/python3"
        ]
        for c in candidates where FileManager.default.isExecutableFile(atPath: c) { return c }
        return "/usr/bin/python3"
    }

    static func detectDocker() -> String {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let candidates = [
            home + "/.orbstack/bin/docker",
            "/usr/local/bin/docker",
            "/opt/homebrew/bin/docker",
            "/Applications/OrbStack.app/Contents/MacOS/xbin/docker"
        ]
        for c in candidates where FileManager.default.isExecutableFile(atPath: c) { return c }
        return "/usr/local/bin/docker"
    }

    // MARK: .env 读写

    func readEnv() -> [String: String] {
        guard let text = try? String(contentsOf: envURL, encoding: .utf8) else { return [:] }
        var out: [String: String] = [:]
        for raw in text.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = String(raw).trimmingCharacters(in: .whitespaces)
            guard !line.isEmpty, !line.hasPrefix("#"), let eq = line.firstIndex(of: "=") else { continue }
            let key = String(line[line.startIndex..<eq]).trimmingCharacters(in: .whitespaces)
            var val = String(line[line.index(after: eq)...]).trimmingCharacters(in: .whitespaces)
            if val.count >= 2, (val.hasPrefix("\"") && val.hasSuffix("\"") || val.hasPrefix("'") && val.hasSuffix("'")) {
                val = String(val.dropFirst().dropLast())
            }
            if !key.isEmpty { out[key] = val }
        }
        return out
    }

    /// 保留注释与顺序地更新 .env：已有的键原位替换，新的键追加到末尾
    func writeEnv(_ updates: [String: String]) {
        let existing = (try? String(contentsOf: envURL, encoding: .utf8)) ?? ""
        var lines = existing.components(separatedBy: "\n")
        var remaining = updates

        for i in lines.indices {
            let line = lines[i].trimmingCharacters(in: .whitespaces)
            guard !line.isEmpty, !line.hasPrefix("#"), let eq = line.firstIndex(of: "=") else { continue }
            let key = String(line[line.startIndex..<eq]).trimmingCharacters(in: .whitespaces)
            if let v = remaining[key] {
                lines[i] = "\(key)=\(v)"
                remaining.removeValue(forKey: key)
            }
        }
        if !remaining.isEmpty {
            if lines.last?.isEmpty == false { lines.append("") }
            for k in remaining.keys.sorted() {
                lines.append("\(k)=\(remaining[k]!)")
            }
        }
        let out = lines.joined(separator: "\n")
        try? out.write(to: envURL, atomically: true, encoding: .utf8)
    }
}

// MARK: - 小工具

enum Shell {
    @discardableResult
    static func run(_ launchPath: String, _ args: [String], cwd: String? = nil, timeout: TimeInterval = 20) -> (Int32, String) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: launchPath)
        p.arguments = args
        if let cwd = cwd { p.currentDirectoryURL = URL(fileURLWithPath: cwd) }
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:" + (env["PATH"] ?? "")
        p.environment = env
        let out = Pipe()
        p.standardOutput = out
        p.standardError = out
        do { try p.run() } catch { return (-1, "spawn failed: \(error.localizedDescription)") }

        let deadline = Date().addingTimeInterval(timeout)
        var data = Data()
        let handle = out.fileHandleForReading

        while p.isRunning && Date() < deadline {
            let chunk = handle.availableData
            if !chunk.isEmpty { data.append(chunk) }
            if chunk.isEmpty { Thread.sleep(forTimeInterval: 0.02) }
        }
        if p.isRunning { p.terminate() }
        p.waitUntilExit()
        let rest = handle.availableData
        if !rest.isEmpty { data.append(rest) }
        return (p.terminationStatus, String(data: data, encoding: .utf8) ?? "")
    }


    static func runData(_ launchPath: String, _ args: [String], timeout: TimeInterval = 20) -> (Int32, Data) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: launchPath)
        p.arguments = args
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:" + (env["PATH"] ?? "")
        p.environment = env
        let out = Pipe()
        p.standardOutput = out
        p.standardError = Pipe()
        do { try p.run() } catch { return (-1, Data()) }
        let sem = DispatchSemaphore(value: 0)
        DispatchQueue.global().async { p.waitUntilExit(); sem.signal() }
        _ = sem.wait(timeout: .now() + timeout)
        let data = out.fileHandleForReading.readDataToEndOfFile()
        return (p.terminationStatus, data)
    }

    static func pgrep(_ pattern: String) -> [Int32] {
        let (code, out) = run("/usr/bin/pgrep", ["-f", pattern])
        guard code == 0 else { return [] }
        return out.split(separator: "\n").compactMap { Int32($0.trimmingCharacters(in: .whitespaces)) }
    }
}

enum Net {
    static func portOpen(host: String, port: Int, timeout: TimeInterval = 0.6) -> Bool {
        var hints = addrinfo(ai_flags: 0, ai_family: AF_INET, ai_socktype: SOCK_STREAM,
                             ai_protocol: IPPROTO_TCP, ai_addrlen: 0, ai_canonname: nil,
                             ai_addr: nil, ai_next: nil)
        hints.ai_flags = 0
        var res: UnsafeMutablePointer<addrinfo>?
        guard getaddrinfo(host, String(port), &hints, &res) == 0, let info = res else { return false }
        defer { freeaddrinfo(res) }
        let fd = socket(info.pointee.ai_family, info.pointee.ai_socktype, info.pointee.ai_protocol)
        guard fd >= 0 else { return false }
        defer { close(fd) }
        var tv = timeval(tv_sec: Int(timeout), tv_usec: 0)
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        let rc = connect(fd, info.pointee.ai_addr, info.pointee.ai_addrlen)
        return rc == 0
    }

    static func getJSON(_ url: URL, timeout: TimeInterval = 6) -> Any? {
        var req = URLRequest(url: url)
        req.timeoutInterval = timeout
        req.cachePolicy = .reloadIgnoringLocalCacheData
        let sem = DispatchSemaphore(value: 0)
        var result: Any?
        let task = URLSession.shared.dataTask(with: req) { data, _, _ in
            if let data = data, !data.isEmpty {
                result = try? JSONSerialization.jsonObject(with: data)
            }
            sem.signal()
        }
        task.resume()
        _ = sem.wait(timeout: .now() + timeout)
        return result
    }

    @discardableResult
    static func postJSON(_ url: URL, body: [String: Any], timeout: TimeInterval = 15) -> Any? {
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        req.timeoutInterval = timeout
        let sem = DispatchSemaphore(value: 0)
        var result: Any?
        let task = URLSession.shared.dataTask(with: req) { data, _, _ in
            if let data = data, !data.isEmpty { result = try? JSONSerialization.jsonObject(with: data) }
            sem.signal()
        }
        task.resume()
        _ = sem.wait(timeout: .now() + timeout)
        return result
    }
}
