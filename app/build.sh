#!/bin/bash
# 构建「鸡蛋收割机.app」—— 原生 macOS APP（Swift + WKWebView，无 Electron / 无 npm 依赖）
# 用法: ./build.sh            仅构建到 ../dist
#       ./build.sh --install  构建并安装到 ~/Applications
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="鸡蛋收割机"
BUNDLE_ID="website.lanche.groupintel"
EXE="GroupIntel"
ROOT="$(cd .. && pwd)"
DIST="$ROOT/dist"
APP="$DIST/$APP_NAME.app"

echo "==> 1/5 编译 Swift 源码"
mkdir -p build "$DIST"
swiftc -O src/*.swift -o "build/$EXE" \
  -framework AppKit -framework WebKit -framework UserNotifications

echo "==> 2/5 组装 .app bundle"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "build/$EXE" "$APP/Contents/MacOS/$EXE"
chmod +x "$APP/Contents/MacOS/$EXE"
# 后端根目录随包记录，APP 启动时据此找 app.py
printf '%s' "$ROOT" > "$APP/Contents/Resources/backendRoot.txt"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>              <string>$APP_NAME</string>
    <key>CFBundleDisplayName</key>       <string>$APP_NAME</string>
    <key>CFBundleIdentifier</key>        <string>$BUNDLE_ID</string>
    <key>CFBundleExecutable</key>        <string>$EXE</string>
    <key>CFBundlePackageType</key>       <string>APPL</string>
    <key>CFBundleShortVersionString</key><string>1.2.0</string>
    <key>CFBundleVersion</key>           <string>3</string>
    <key>CFBundleIconFile</key>          <string>AppIcon</string>
    <key>LSMinimumSystemVersion</key>    <string>12.0</string>
    <key>LSApplicationCategoryType</key> <string>public.app-category.productivity</string>
    <key>NSHighResolutionCapable</key>   <true/>
    <key>NSSupportsAutomaticGraphicsSwitching</key><true/>
    <key>NSHumanReadableCopyright</key>  <string>本地群情报工作台</string>
    <key>NSAppTransportSecurity</key>
    <dict>
        <key>NSAllowsLocalNetworking</key><true/>
        <key>NSAllowsArbitraryLoads</key><true/>
    </dict>
</dict>
</plist>
PLIST

echo "==> 3/5 生成图标"
SRC_ICON="$ROOT/electron-app/assets/icon.png"
if [ -f "$SRC_ICON" ]; then
  ICONSET="build/AppIcon.iconset"
  rm -rf "$ICONSET"; mkdir -p "$ICONSET"
  for s in 16 32 128 256 512; do
    sips -z "$s" "$s" "$SRC_ICON" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null 2>&1
    d=$((s * 2))
    sips -z "$d" "$d" "$SRC_ICON" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null 2>&1
  done
  iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
  echo "    AppIcon.icns 已生成"
else
  echo "    跳过（未找到 electron-app/assets/icon.png）"
fi

echo "==> 4/5 临时签名"
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || echo "    codesign 失败（不影响本机运行）"
xattr -cr "$APP" 2>/dev/null || true

echo "==> 5/5 完成"
du -sh "$APP" | awk '{print "    体积: " $1}'
echo "    APP:  $APP"

if [ "${1:-}" = "--install" ]; then
  mkdir -p "$HOME/Applications"
  rm -rf "$HOME/Applications/$APP_NAME.app"
  cp -R "$APP" "$HOME/Applications/"
  echo "    已安装: $HOME/Applications/$APP_NAME.app"
fi

echo
echo "启动: open \"$APP\""
echo "自检: \"$APP/Contents/MacOS/$EXE\" --check"
