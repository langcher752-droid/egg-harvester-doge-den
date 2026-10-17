// 把一组 SF Symbols 渲染成 PNG，供 Web UI 当图标用（替代 emoji）。
//
//   swift tools/render-symbols.swift <输出目录>
//
// 输出：<name>@2x.png（默认 18pt 大小、2 倍图 → 36px），颜色用 slate-500 #64748b，
// 以及 <name>-white@2x.png 供深色底按钮使用。模板渲染（isTemplate=false）即可，
// Web 端一律通过 CSS filter/颜色统一，不做二次着色。

import AppKit
import Foundation

let weight = NSFont.Weight.medium
let pointSize: CGFloat = 18
let scale: CGFloat = 2

// name: SF Symbol 名；alias: 输出文件名
let icons: [(String, String)] = [
    // 侧栏
    ("key.fill", "keys"),
    ("bolt.fill", "stream"),
    ("doc.text.magnifyingglass", "intel"),
    ("folder.fill", "files"),
    ("person.2.fill", "groups"),
    ("stethoscope", "providers"),
    ("gearshape.fill", "settings"),
    ("arrow.clockwise", "refresh"),
    ("trash", "trash"),
    ("doc.on.doc", "copy"),
    ("link", "link"),
    ("square.and.arrow.down", "download"),
    ("magnifyingglass", "search"),
    ("checkmark", "check"),
    ("xmark", "close"),
    ("clock", "clock"),
    ("photo", "image"),
    ("text.bubble", "message"),
    ("exclamationmark.bubble", "important"),
    ("plus", "plus"),
    ("chevron.down", "chevron-down"),
    ("play.fill", "play"),
    ("arrow.triangle.2.circlepath", "retest"),
    ("bubble.left.and.text.bubble.right", "context"),
    ("doc.plaintext", "log"),
    ("shippingbox", "cache"),
    ("chart.bar.xaxis", "chart"),
    ("bell.badge", "notification"),
    ("checkmark.seal", "online"),
    ("person.crop.circle", "account"),
    ("basket.fill", "harvest"),
    ("sparkles", "sparkles"),
    ("square.and.arrow.up", "export"),
    ("info.circle", "info"),
    ("chart.line.uptrend.xyaxis", "trend"),
    ("brain.head.profile", "ai"),
    ("doc.text", "doc"),
    ("dollarsign.circle", "money"),
    ("arrow.up.right.square", "external"),
    ("eye", "eye"),
    ("pin", "pin"),
    ("lock.shield", "shield"),
    ("bolt.badge.clock", "urgent"),
    ("pawprint.fill", "doge"),
    ("arrow.up.left.and.arrow.down.right", "fullscreen"),
    ("folder", "folder"),
]

let outDir = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "static/icons"
try? FileManager.default.createDirectory(atPath: outDir, withIntermediateDirectories: true)

func render(_ symbol: String, color: NSColor, to path: String) -> Bool {
    guard let image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil) else {
        print("!! 找不到符号 \(symbol)")
        return false
    }
    let config = NSImage.SymbolConfiguration(pointSize: pointSize, weight: weight)
    guard let configured = image.withSymbolConfiguration(config) else { return false }

    let size = configured.size
    let pxW = Int(ceil(size.width * scale))
    let pxH = Int(ceil(size.height * scale))
    guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil,
                                     pixelsWide: pxW, pixelsHigh: pxH,
                                     bitsPerSample: 8, samplesPerPixel: 4,
                                     hasAlpha: true, isPlanar: false,
                                     colorSpaceName: .deviceRGB,
                                     bytesPerRow: 0, bitsPerPixel: 0) else { return false }
    rep.size = NSSize(width: size.width, height: size.height)

    NSGraphicsContext.saveGraphicsState()
    guard let ctx = NSGraphicsContext(bitmapImageRep: rep) else {
        NSGraphicsContext.restoreGraphicsState()
        return false
    }
    NSGraphicsContext.current = ctx
    ctx.imageInterpolation = .high

    let rect = NSRect(x: 0, y: 0, width: size.width, height: size.height)
    configured.draw(in: rect, from: .zero, operation: .sourceOver, fraction: 1.0)
    // 用 symbol 颜色覆盖：先把绘制结果当作遮罩染色
    color.set()
    rect.fill(using: .sourceAtop)

    NSGraphicsContext.restoreGraphicsState()

    guard let data = rep.representation(using: .png, properties: [:]) else { return false }
    try? data.write(to: URL(fileURLWithPath: path))
    return true
}

let normal = NSColor(srgbRed: 0.392, green: 0.455, blue: 0.545, alpha: 1)  // #64748b
let white = NSColor.white

var ok = 0
for (symbol, alias) in icons {
    if render(symbol, color: normal, to: "\(outDir)/\(alias)@2x.png") { ok += 1 }
    _ = render(symbol, color: white, to: "\(outDir)/\(alias)-white@2x.png")
}
print("已渲染 \(ok)/\(icons.count) 个符号 → \(outDir)")
