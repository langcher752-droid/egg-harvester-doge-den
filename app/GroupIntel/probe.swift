import Cocoa
import WebKit

final class Delegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var web: WKWebView!

    func applicationDidFinishLaunching(_ n: Notification) {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 800),
                          styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        window.title = "probe"
        web = WKWebView(frame: window.contentView!.bounds)
        web.navigationDelegate = self
        window.contentView!.addSubview(web)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        web.load(URLRequest(url: URL(string: "http://127.0.0.1:8890/")!))
        DispatchQueue.main.asyncAfter(deadline: .now() + 25) {
            print("PROBE_TIMEOUT")
            NSApp.terminate(nil)
        }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        print("PAGE_LOADED title=\(webView.title ?? "nil")")
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
            let cfg = WKSnapshotConfiguration()
            cfg.rect = webView.bounds
            webView.takeSnapshot(with: cfg) { img, err in
                if let img = img, let tiff = img.tiffRepresentation,
                   let rep = NSBitmapImageRep(data: tiff),
                   let png = rep.representation(using: .png, properties: [:]) {
                    try? png.write(to: URL(fileURLWithPath: "/tmp/probe_snapshot.png"))
                    print("SNAPSHOT_OK \(png.count) bytes \(Int(img.size.width))x\(Int(img.size.height))")
                } else {
                    print("SNAPSHOT_FAIL \(String(describing: err))")
                }
                NSApp.terminate(nil)
            }
        }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        print("NAV_FAIL \(error.localizedDescription)")
        NSApp.terminate(nil)
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        print("PROVISIONAL_FAIL \(error.localizedDescription)")
        NSApp.terminate(nil)
    }
}

let app = NSApplication.shared
let d = Delegate()
app.delegate = d
app.setActivationPolicy(.regular)
app.run()
