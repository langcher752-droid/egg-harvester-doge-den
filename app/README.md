# 鸡蛋收割机.app —— macOS 原生客户端

把「群情报 + API Key 收割」整套系统装进一个可双击的 macOS APP：**自带后端与采集服务的自启动、QQ 扫码登录、账号与模型配置、重要情报提醒、状态栏常驻**。

- 体积约 550 KB，**原生 Swift + WKWebView**，不用 Electron / 不需要 npm 依赖
- 界面就是现有 Web 控制台，APP 负责「拉起、托管、托盘、登录、提醒」
- 已安装位置：`~/Applications/鸡蛋收割机.app`（源码与构建脚本在本目录）

---

## 1. 安装与构建

```bash
cd "$PROJECT_ROOT/app"
./build.sh --install          # 编译 + 打包 + 复制到 ~/Applications
open ~/Applications/鸡蛋收割机.app
```

只构建不安装：`./build.sh`（产物在 `../dist/鸡蛋收割机.app`）。

构建依赖：Xcode Command Line Tools 自带的 `swiftc`（本机已具备，Swift 6.2 / macOS SDK 26）。

---

## 2. APP 帮你做了什么

### 2.1 打开即全启动
启动时按顺序拉起并每 5 秒巡检一次：

| 服务 | 作用 | 端口 |
| --- | --- | --- |
| backend | FastAPI 后端（`uvicorn app:app`） | 8890 |
| wechat-watch | 微信本地库采集 | — |
| web-watch | AI 福利日报 / 白嫖论坛采集 | — |
| intel-watch | 情报简报（6h）+ 重要事件检测（5min） | — |
| napcat | QQ 协议端 Docker 容器 | 3000 / 6099 |

- 已经被 launchd 拉起的服务会识别为「运行中（launchd 托管）」，**不会重复启动**
- APP 自己拉起的进程在退出时会被收掉；launchd 托管的保持运行
- 「服务」菜单或「服务状态」窗口可逐个 启动 / 重启 / 停止，还能直接看日志尾巴

### 2.2 开机自启（全链路）
- APP 自身：`~/Library/LaunchAgents/website.lanche.groupintel-app.plist`（`RunAtLoad`），在「服务 → 开机自启」勾选/取消
- 后端与采集：沿用已有的 launchd 作业
  - `website.lanche.egg-harvester`（后端）
  - `website.lanche.web-watch`
  - `website.lanche.wechat-watch`
  - `website.lanche.intel-watch`
- 结果：开机后无需任何点击，采集与简报都在跑；APP 只是把界面和状态摆出来

### 2.3 登录账号（应用菜单 → 账号与登录…）
- **QQ**：实时显示 NapCat 容器里的登录二维码（每 10 秒自动刷新），手机 QQ 扫码即完成登录；已登录时显示 `🟢 已登录：昵称（QQ 号）`
  - 另有「重启 NapCat」「打开 NapCat 管理页(http://127.0.0.1:6099/webui)」
- **微信**：`WECHAT_WXID` / `WECHAT_CODE` 两个字段直接写 `.env`（key 由 `md5(code + wxid)[:16]` 推出）
- **LLM 网关**：`GATEWAY_BASE` / `GATEWAY_KEY` / `VISION_MODEL` / `SUMMARY_MODEL` 可视化修改
- 「💾 保存并重启采集」= 改写 `.env`（保留注释与顺序）+ 重启后端与采集进程，配置立即生效

### 2.4 重要情报提醒
- 后端 `intel-watch` 每 5 分钟用 LLM 判断重要事件，写入 `important_events`
- APP 每 10 秒轮询 `/important_events?unnotified_only=true`：
  - 有系统通知权限 → 走 macOS 通知中心（点通知直达「结构化简报」面板）
  - 无权限 → **应用内右上角浮层卡片**（14 秒自动消失，点卡片跳转），不依赖任何授权
- 首次运行会把历史事件标记为已读，避免开机轰炸
- 「服务 → 测试提醒卡片」可随时预览浮层效果；「通知权限设置…」直通系统设置

### 2.5 菜单与状态栏
菜单栏常驻 `🥚 5/5`，下拉即可看到每个服务状态、一键重启、扫码登录、退出 APP。
主窗口关闭不退出（与 Telegram 一致），点 Dock 图标或状态栏「显示主窗口」恢复。

| 菜单 | 内容 |
| --- | --- |
| 鸡蛋收割机 | 关于、账号与登录 ⌘,、服务状态 ⌘S、隐藏、退出 ⌘Q |
| 视图 | 重新加载 ⌘R、强制重新加载 ⇧⌘R、六个面板直达、打开数据/日志目录、在浏览器中打开 |
| 服务 | 启动全部、重启全部、服务状态、QQ 扫码登录 ⌘L、测试提醒卡片、通知权限设置、开机自启 |
| 窗口 | 主窗口 ⌘1、最小化 ⌘M |

### 2.6 与网页版的差异
- 下载：`WKDownload` 接管，落盘走系统「存储为」面板；页面内的进度条逻辑照常工作
- 新窗口链接（如外部站点）交给默认浏览器打开
- 注入 `window.__GROUPINTEL_APP__ = true`，方便页面里区分 App 与浏览器
- 已有实例在跑时，再次双击不会开第二个进程，只会把旧窗口唤到前台

---

## 3. 自检命令（不需要点界面）

```bash
APP=~/Applications/鸡蛋收割机.app/Contents/MacOS/GroupIntel
$APP --check                       # 后端路径 / python / 端口 / docker / NapCat 探测
$APP --services                    # 五个服务的实时状态（含 pid、日志文件名）
$APP --snapshot /tmp/ui.png        # 内置浏览器整页截图（验证界面是否正常渲染）
$APP --snapshot /tmp/p.png --goto intel   # 先切到指定面板再截图（keys/stream/intel/files/groups/settings）
$APP --account-image /tmp/acc.png  # 账号与登录窗口截图（含实时二维码）
$APP --toast-image /tmp/toast.png  # 提醒卡片外观截图
```

本轮结果：`--check` 全绿；`--services` 5/5 运行中；三个截图均正常（见对话附图）。

---

## 4. 源码结构

```
app/
├── build.sh                     # 编译 + 打 .app + icns + ad-hoc 签名 + 可选安装
└── src/
    ├── main.swift               # 入口、单实例保护、命令行自检
    ├── Paths.swift              # RuntimeConfig 探测（后端目录/python/docker/端口）、.env 读写、Shell/Net 工具
    ├── Services.swift           # 服务模型 + 巡检/启停/日志/QQ 在线探测
    ├── Notifications.swift      # 事件轮询 → 系统通知或应用内卡片；AppState 深链
    ├── ToastCenter.swift        # 右上角提醒浮层（NSVisualEffectView 圆角卡片）
    ├── MainWindow.swift         # WKWebView 主窗口、启动遮罩、下载接管、新窗口外链
    ├── AccountWindow.swift      # QQ 二维码 / 微信凭据 / 网关配置
    ├── ServicesWindow.swift     # 服务状态表 + 日志查看器
    └── AppDelegate.swift        # 菜单、状态栏、开机自启、账号与服务窗口
```

运行配置可覆盖：`~/Library/Application Support/GroupIntel/app.json`
（`backendRoot` / `pythonPath` / `port` / `napcatContainer` / `dockerPath` / `autoStartApp` / `manageNapCat`），首次运行自动探测并落盘。

---

## 5. 排障

| 现象 | 处理 |
| --- | --- |
| 界面卡在「正在启动本地服务」 | 看 `logs/app.err.log`；确认 `:8890` 未被占用：`lsof -nP -iTCP:8890 -sTCP:LISTEN` |
| QQ 显示未登录 | 账号窗口点「重启 NapCat」，等 10 秒扫码；容器需 `0.0.0.0:3000->3000` 端口映射 |
| 扫码后仍离线 | `docker logs --tail 50 napcat` 看协议端日志；必要时「重启 NapCat」重新拿码 |
| 收不到系统通知 | 用应用内浮层即可；或在「通知权限设置…」里允许，再点「测试提醒卡片」验证 |
| 双击 APP 没反应 | 已有实例在跑（单实例保护），看 Dock / 状态栏 |
| 想彻底停采集 | 「服务 → 全部停止」，再 `launchctl bootout gui/$(id -u)/website.lanche.egg-harvester` 等 |

---

## 6. 本轮验收

- `--check`：后端 `app.py` 存在、python 可执行、8890 监听、docker 可执行、NapCat 容器名 `napcat`、3000 已映射
- `--services`：backend / wechat-watch / web-watch / intel-watch / napcat 全 🟢
- `--snapshot`：鸡蛋库面板 3 张 Key 卡片、统计条 6,679 条消息、撤回保命 13
- `--account-image`：二维码取自容器 `/app/napcat/cache/qrcode.png`，微信 wxid/code 与网关四项已回填
- `--toast-image`：提醒卡片标题/正文/关闭按钮排版正确
- 开机自启：`launchctl print gui/501/website.lanche.groupintel-app` 存在且 `RunAtLoad`，指向 `~/Applications/鸡蛋收割机.app`

---

## 7. 交付当轮补充修复（2026-10-17 10:5x）

| 问题 | 修复 |
| --- | --- |
| 设置面板卡片被 `margin:0 auto` 挤成内容宽度、模型名显示不全 | `#view-settings .table-card{width:100%;margin:0}` + 输入框 `width:100%` |
| 侧栏计数切面板后归零（简报/文件/群组） | 新增 `updateBadges()`，每次切换面板刷新四个徽章（20s 节流） |
| 守护进程表把「重要事件通知」列为未运行（该功能已内置到 APP） | 列表改为 后端服务 + 三个采集守护，与实际一致 |
| `NAP_HTTP_URL` 指向旧容器 IP | 改为 `http://127.0.0.1:3000` 或在 `.env` 中填写当前 OneBot 地址 |
| `--services` 把 NapCat 称为「后台 launchd」 | 新增 `Service.stateLabel`：容器 →「运行中（容器）」，其余 →「运行中（launchd 托管）」 |
| 再次执行 `launchctl bootout` 后登录项未重新加载 | 现网已 `bootstrap`，`launchctl list` 可见 `website.lanche.groupintel-app`，登录即拉起 |

面板切换已实测：`--snapshot --goto intel/settings/stream` 三张截图均正常（简报页显示 10 条 AI 判定的事件）。

---

## 8. 自启动与自动登录（2026-10-17 11:0x 加固）

**开机/登录后自动拉起整条链路**

| 层 | 实现 | 验证 |
| --- | --- | --- |
| APP | `~/Library/LaunchAgents/website.lanche.groupintel-app.plist`：`/usr/bin/open -a <app>`，RunAtLoad | 卸载后重新 bootstrap，8 秒内出现 APP 进程 |
| 后端 + 3 个采集 | launchd：`website.lanche.egg-harvester / web-watch / wechat-watch / intel-watch` | `launchctl list` 全部在册，--services 5/5 |
| NapCat 容器 | docker `--restart always` + 端口 3000/6099 映射 | `docker inspect` → RestartPolicy=always |
| Docker 引擎 | APP 巡检发现 docker 不可达时自动 `open -a OrbStack` 并等待就绪（60s 节流），无需手动开 | `ServiceSupervisor.ensureDocker()` |

**QQ 自动登录**：首次仍需用手机扫码（一次性）。登录成功后 APP 自动把账号写进容器内 `napcat/config/webui.json` 的 `autoLoginAccount`，登录态保存在 `/app/.config/QQ` 卷里；此后重启容器/重启电脑都会自动登录，无需再扫码。

## 9. Web UI 内直接扫码登录 QQ（2026-10-17 11:1x）

侧栏底部新增登录胶囊：`🟡 QQ 未登录 · 点此扫码` / `🟢 QQ 已登录 · 昵称`，每 30 秒自动刷新。

点击后弹出扫码窗口（不用再切到原生窗口）：
- 二维码来自 `GET /qq/qrcode`（后端 `docker cp napcat:/app/napcat/cache/qrcode.png`，5 秒缓存）
- `GET /qq/status` 返回 `{online, uin, nickname, qr_age, qr_fresh}`；弹窗每 5 秒轮询，登录成功自动关闭并提示
- 「🔄 刷新二维码」「♻️ 重启 NapCat」两个按钮兜底（`POST /qq/restart`）

登录成功后，APP 会自动把账号写进容器 `webui.json` 的 `autoLoginAccount`，**以后重启容器/电脑免扫码自动登录**。

**后端归属收敛**：`ServiceSupervisor.start()` 现在先查登录项（`website.lanche.egg-harvester / web-watch / wechat-watch / intel-watch`），存在就只做 `launchctl kickstart`，绝不再自己起第二份 —— 修掉了“APP 自起 uvicorn 与 launchd 抢 8890、launchd 作业一直 exit 3”的隐患。开机自检：`launchctl list | grep egg-harvester` 应显示 PID，`pgrep -f "uvicorn app:app"` 只应有 1 个且父进程是 launchd。

---

## 10. 交互补齐（2026-10-17 11:3x）

| 反馈 | 修法 |
| --- | --- |
| 事件/简报「查看详情」点不进 | 新增 `GET /messages/context?id&before&after`、`GET /messages/by_ids?ids`、`GET /messages?start_ts&end_ts`；三个入口（消息 ⋯、事件「查看上下文」、简报「查看上下文」）统一打开右侧抽屉，目标消息高亮 |
| 情报里的网址点不开 | 前端 `linkify()` 把 http(s) 渲染成蓝色虚线下划线链接，点击不冒泡到抽屉；`MainWindow` 新增导航策略：非本机地址交给 `NSWorkspace` 用系统浏览器打开 |
| 通知点进来看不到详情 | 浮层/系统通知改为深链 `/?event=<id>`；`AppState.openInMainWindow` 解析 query 后调用 `openEventById()`，直接切简报页并弹出该事件原文 |
| 系统原生控件不统一 | 全局 `select` / `input[type=checkbox]` 统一自绘（圆角、蓝色选中、无系统蓝块）；设置页改两列栅格 |
| 右侧黑边 | 抽屉静止态 `box-shadow:none` + `color-scheme: light`（滚动条不再画深色轨道） |
| 表情包太多 | `exclude_sticker` 默认开启（前端「🙈 隐藏表情包」勾选并记忆），后端过滤 `msg_type=sticker`、纯占位符消息、以及含 `[动画表情]/[表情包]` 的图片；情报截图不受影响 |
| 设置里没有 log | 新增 `GET /logs?name&lines`（白名单 `logs/*.log`，尾部最多 400KB），设置页底部「📜 运行日志」卡片可选文件、刷新、自动滚到底 |
| 抽屉关不干净 | 遮罩改调 `closeAnyDrawer()`，同时关掉收割抽屉与上下文抽屉 |

自检新增：`--eval <js>`（在页面里执行 JS 并打印结果）、`--snapshot --goto <panel> --js <js>`（先切面板/执行 JS 再截图）。

---

## 11. 情报流改版 + curl 日志（2026-10-17 11:5x）

| 反馈 | 改法 |
| --- | --- |
| 「每次消息刷新都很鬼畜」 | 三条措施：①`applyStream()` 对 `id:read\|recalled` 做签名，内容没变就**完全不碰 DOM**（此前每 5 秒重建 innerHTML，图片跟着重载，所以闪）；②新消息到达且用户不在底部时，**不重建**，只在底部弹「↑ 有新消息（N 条）」，点击才刷新并滚到底；③会话列表同样做签名差量，避免每 5 秒重排。实测标记节点在 7 秒刷新后仍存活（`原节点存活 是（未重建 DOM）`）。 |
| 「查看群消息应该像聊天软件左边有群列表」 | 情报流重做为 IM 双栏：左侧会话列表（头像、群名、最后一条消息、时间、未读数、`全部/QQ/微信/Web` 分类 chip、搜索），点击即只看该群；右侧标题栏 + 会话内搜索 + 消息流。顶部不再有「全部平台/全部群组」下拉。 |
| 「检测鸡蛋要显示 curl 的 log」 | `probe.py` 现在把真实请求还原成 `curl`（Key 脱敏）并逐条记录 `trace`（step / status / ms / 响应前 300 字）；`/harvest`、`/keys/test`、`pipeline.process_text` 全链路透传。手工收割结果里每条候选直接展示 `$ curl …` 与 `→ GET /v1/models 200 · 1234ms`；点「重新测速」会弹出「测速日志 · curl」抽屉。 |

配套：`--snapshot … --wait <ms>` 自检可等自动刷新后再截图（用于验证不闪）。

---

## 12. 体验补课（2026-10-17 12:0x–12:3x）

| 反馈 | 修法 |
| --- | --- |
| 右上角提醒卡片太大、不自动关、圆角外还有半透明方块 | `ToastWindow` 换成纯色 `NSView`（`controlBackgroundColor` + 1px 描边 + 12pt 圆角），宽度 360→320、正文最多 3 行；**8 秒自动消失**，底部加了 2pt 倒计时进度条 |
| 点提醒就把主窗口整个弹出来、窗口偏大 | 主窗口默认 1200×800（`minSize` 1020×640，autosave 换到 `GroupIntelMainWindow2`），点击只做「显示 + 深链」，最小化时会先 deminiaturize，不再放大 |
| 消息顺序反了 | 后端按 `ts DESC` 返回，前端渲染前 `messages.slice().reverse()`：**旧的在上、新的在下**，并在底部自动滚到底 |
| 时间标注要像聊天软件 | `fmtSmartTime()`：刚刚 / N 分钟前 / N 小时前（当天）/ 昨天 HH:MM / N 天前 / MM-DD HH:MM；鼠标悬停显示精确时间 |
| 新消息没有动画 | 新增 `@keyframes msgIn`，比上次最新 id 新的气泡加 `.msg-fresh` 播放入场动画 |
| 已读后红点不消失（70 未读） | 打开会话时自动调 `POST /messages/mark_read`（10s 节流）并刷新会话列表；点单个气泡也会把该条标已读。实测 `unread` 99 → 0 |
| 图片预览点开没有放大和其他选项 | 灯箱重写：滚轮/按钮缩放（20%–600%）、拖拽平移、单击图片 1x↔2x、`适应`、`在浏览器打开`、`下载`、ESC 关闭 |
| Cmd+C 复制不了 | 菜单栏补了标准「编辑」菜单（撤销/剪切/拷贝/粘贴/粘贴为纯文本/全选/查找），走 AppKit 响应链，WKWebView 里 Cmd+C / Cmd+V / Cmd+A 恢复 |
| 「Jimmy 镜明 撤回了一条消息」文案不对（微信不存撤回） | 微信消息的撤回标记改文案为「已删/撤回（微信仅能识别占位）」，QQ 仍显示「已撤回」 |
| 想点开白嫖帖原帖 | 白嫖社区消息按 `msg_key` 里的帖子 id 生成「查看原帖 ↗」，外链走系统浏览器 |
| 想要群头像 / 个人头像（压缩） | 新增 `GET /avatar/{group|user}/qq/{id}`：从 qlogo CDN 下载 → `sips -Z 96` 压到 96px → 缓存在 `media/avatars/`（7 天）；会话列表和消息气泡都换成真实头像，非 QQ / 失败自动回退首字母色块 |
| 鸡蛋失效要问是否删除 | 测速失败弹「这个鸡蛋不能用了」确认框；每张 Key 卡片也有「删除」 |
| 检测鸡蛋时要 curl log | 每张 Key 卡片新增「🧾 测速日志」，点「重新测速」后直接在卡片里展开 curl + 逐步响应（401 的 body 原样显示） |
| 需要 DSH / WorkBuddy / CCswitch 供应商体检 | 新侧栏「🩺 供应商体检」：`providers.py` 读 DSH `~/.dsh/settings*.yaml`、WorkBuddy `~/.workbuddy/models.json`（按 url+key 归组）、CCswitch `~/.cc-switch/cc-switch.db`；一键并发体检，显示 ✓ 延迟/模型数 或 ✗ HTTP 码；「删除失效的」会**先备份文件**再改，DSH 改完用 `yaml.safe_load` 校验，失败自动回滚。实测 20 个 provider：15 可用 / 5 失效（3 个本地 ccswitch 代理 502、2 个 156.225.31.92 超时） |
| 缓存要能查/能删/能换位置/能自动清 | 设置页新增「🗄 媒体缓存」：文件数与总大小、按类型分布、目录、`在访达打开`、清理 7 天前 / 只留最近 500MB / 全部清空（都要确认），自动清理策略存 `settings.cache_auto_days`，由 intel-watch 每 6 小时执行一次 |

---

## 13. UI 规范：SF Symbols 图标 + 统一栅格（2026-10-17 13:1x–13:4x）

**图标体系（不再用 emoji）**

- `tools/render-symbols.swift`：把 SF Symbols 渲染成透明 PNG。用法 `swift tools/render-symbols.swift static/icons`，输出 `<alias>@2x.png`（36px，染色 `#475569`）与 `<alias>-white@2x.png`。
- 图标别名（40+）：`keys / stream / intel / files / groups / providers / settings / refresh / trash / copy / link / download / search / check / close / clock / image / message / important / plus / chevron-down / play / retest / context / log / cache / chart / notification / online / account / harvest / sparkles / export / info / trend / ai / doc / external / eye / pin / shield / urgent`。
- 前端用法：`<i class="ico" style="--ico:url(/static/icons/keys@2x.png)"></i>`。`.ico` 用 `-webkit-mask` + `background-color: currentColor`，所以图标自动跟随文字颜色（导航选中变蓝、删除按钮变红）。
- 后端 `app.py` 挂了 `app.mount("/static", StaticFiles(directory=static_dir), name="static")`（在 `/media` 之前），`/static/icons/*.png` 直接可取。
- macOS 指针资源：`static/macos-kit/`（Apple macOS 27 UI Kit 的 22 个 `Pointers/*.svg`），已用 `sips` 转成 `static/macos-kit/cursors/*.png`，用在输入框（文本光标）、灯箱（Zoom In/Out）、拖拽（Hand Grabbing）。

**视觉规范（写在 `templates/index.html` 尾部“统一视觉规范”块）**

| 部位 | 规范 |
| --- | --- |
| 设计变量 | `--ctl-h: 32px`、`--radius: 8px`、`--radius-card: 12px`、`--line: #e5e9f0`、`--line-2: #eef1f5`、`--ink-1/2/3` |
| 顶栏 | 56px 高，标题 15px/600，副标题 11.5px；统计胶囊与「刷新」按钮**同高 30px** |
| 卡片 | 12px 圆角、1px 描边、`0 1px 2px rgba(15,23,42,.04)` 阴影、16–18px 内边距 |
| 按钮 | 统一 32px 高、8px 圆角、12.5px 字；`.btn-primary` 绿 `#15803d`、`.btn-ghost` 浅灰、`.btn-action.danger` 红字浅底、`.btn-danger` 红底 |
| 输入框 | 32px 高、`#f8fafc` 底、聚焦蓝描边 + 3px 光晕 |
| 侧栏 | 图标 16px + 文案 13px，角标 `font-mono`；**角标为 0 或无效时自动隐藏**（不再出现灰色 0 / -）；底部按钮 34px |
| 空状态 | `.empty-state`：虚线卡片 + 26px 灰度图标 + 标题 13.5px + 说明 12px |
| 消息流 | `.stream-head` / `.stream-toolbar` 固定不滚动（toolbar 白底 + 下边框 + `z-index:3`），气泡不再压到搜索框；单会话视图隐藏消息上的平台标签与会话名；新消息胶囊改为**绝对定位居中**，只在真的往上翻历史时出现，图片异步加载后会保持贴底（不再误报“有新消息”） |
| 供应商体检 | 行栅格 `18px 80px minmax(110px,.7fr) minmax(190px,1.3fr) minmax(150px,auto) auto`，URL/操作按钮不再溢出 |
| 设置页 | `.table-card` 统一 12px 圆角 + 16/18px 内边距，输入框 32px、表格 12.5px；媒体缓存统计改成「文件数 / 占用 / 最早 + 类型标签」结构 |

**已知待办**：品牌 logo / App 图标仍用占位（用户稍后提供），到货后替换 `static/icons/` 与 `app/Resources/AppIcon.icns`。

---

## 14. 品牌图标 + 供应商体检数据源修复（2026-10-17 13:3x–13:5x）

**App 图标 / logo**

- 设计稿导出在 `~/Downloads/Icon Exports`（macOS 各尺寸 + Dark/Tinted/Clear 变体）。取 `Icon-macOS-Default-1024x1024@1x.png` 作为母版：
  - `electron-app/assets/icon.png` → `build.sh` 用它 sips 出全套尺寸 → `Contents/Resources/AppIcon.icns`（1024 起，16/32/128/256/512 双倍图）
  - `static/brand/app-{32,64,128,256}.png` → favicon（`/static/brand/app-64.png`）与备用图
  - `static/brand/glyph-{36,64,128,256}.png` → 侧栏 36px 品牌位（用 Pillow 把角色从图标白底里裁出来，非白色像素求 bbox + 4% 留白 + 正方形补边）
- 侧栏 `<div class="brand-icon"><img src="/static/brand/glyph-128.png">`，9px 圆角、无灰底。

**供应商体检：之前只读到 3 个，现在 54 个**

- 根因：`list_dsh()` 只扫 `~/.dsh/settings*.yaml`，而 **DSH Desktop 真正的 provider 清单在 `~/.dsh/profiles/<profile>/cordis.patch.yml`**（`- id: llm-pi-ai` 那条 patch 的 `config.providers`）。desktop profile 就有 28 个，settings*.yaml 里只剩 3 个早年残留的 `cc-switch`。
- 现在：
  - 扫 `~/.dsh/profiles/*/cordis.patch.yml`，用**支持 `!!js` 标签的 SafeLoader 子类**（`_DshLoader` + `add_multi_constructor("tag:yaml.org,2002:js")`）解析；递归 `_walk_providers()` 找任意层级的 `providers:` 字典
  - 再兼容扫 `~/.dsh/settings*.yaml`，按 `(base_url, api_key, name)` 去重
  - `apiKeyEnv` 从 `~/.dsh/.credentials.yaml` 的 `refs` 解析，解析不到时把 env 名字带进错误（`未配置 API Key（X_API_KEY 在 ~/.dsh/.credentials.yaml 里没有值）`），不再发出 `Bearer ` 空头
  - 实测：DSH 37（desktop 28 + wbpj 10 + gemini/grok/multi 各 2，去重后 37）· WorkBuddy 9 · CCswitch 8 = **54**
- 删除也修了：`_delete_dsh()` 现在是**按缩进块删**，同时适配 `providers:` 在顶层（settings）和缩进 4（cordis patch）两种布局；删完用带 `!!js` 支持的 loader 重新解析，**provider 数必须正好少 1 且该 key 消失**，否则从备份回滚。已在 `/tmp` 副本上验证 28→27。

**供应商面板 UI 重做（原来那排确实怪）**

- 行结构：`[勾选] [来源 chip] [名称 + 副标题] [baseURL] [协议 + 掩码 Key] [状态] [测/删]`，7 列栅格，状态右对齐、不跟按钮挤在一起。
- 副标题：DSH 显示 `profile · provider 名 · N 模型`，WorkBuddy 显示 `host` + `N 个模型 · models.json`（之前两行都是“7 个模型”重复），CCswitch 显示 `cc-switch · 名称`。
- 分类 chip 带数量：`全部 54 / DSH 37 / WorkBuddy 9 / CCswitch 8`；汇总行显示 `共 54 个 provider：DSH 37（desktop 28）· WorkBuddy 9 · CCswitch 8 · 已测 54 个，失效 16 个`。
- 实测结果：54 个里 38 可用、16 失效（3 个空 Key 的 wbpj、cloudflare 405、baipiao/ag/gpt/xianqiao 401/429、2 个超时等）。

---

## 15. 供应商面板排版重排（2026-10-17 13:48 反馈）

> 用户反馈：“这里 UI 还是很挤”。原因是 7 列都塞在一行里（勾选 / 来源 / 名称 / URL / 协议+Key / 状态 / 操作），1000px 卡片里每个字段只剩 100 多像素。

- **行改成 5 列**：`[勾选] [来源 chip] [名称 + 副标题] [协议 + 掩码 Key] [状态 + 测/删]`；`grid-template-columns: 18px 78px minmax(0,1fr) minmax(150px,176px) auto`，列间距 16px、行内边距 11px 16px、行间距 10px。
- **baseURL 下移到副标题**，和 `profile · N 模型` 同一行（`https://api.deepseek.com · desktop · 28 模型`），完整的 URL 放 `title` 悬浮提示，长度不再挤压主标题。
- **头部拆两层**：第一行左边是过滤 chip（带计数），右边是「只看失效 / 全部体检 / 删除失效的」，中间一条 `1px` 分隔线；第二行是汇总（`共 54 个 provider：DSH 37（desktop 28）· WorkBuddy 9 · CCswitch 8 · 已测 54 个，失效 16 个`）。卡片内边距 20px 22px。
- **状态着色**：成功行白底；失败行淡红底 + 红描边；只缺 Key（不是真失败）淡黄底 + `✗ 未配置 Key`，不再显示误导性的“连接失败 · 0ms”。
- `providerTesting` 加锁，连点「全部体检」不会并发两轮。
- 验证：`只看失效` 正确筛出 16 行；`check_system` 27/27、`test_e2e` 12/12。


## 16. 交互四项修复（2026-10-17 14:2x 反馈）

- **markdown 换行空格**：`initMarkdown()` 的 hljs 门槛（CDN 从未加载）导致 breaks 配置整体被跳过；改为只 gate 在 marked 上、`marked.use({gfm,breaks})`，`renderMarkdown()` 先清行尾空格。简报 bullet 恢复逐行。
- **查看上下文跳到消息流**：上下文不再开弹窗/抽屉，改为切换 stream 面板进入上下文模式（`.ctx-banner` 显示标题/来源/『AI 总结上下文』/『返回实时会话』），目标消息高亮居中。
- **AI 总结上下文（手动）**：`POST /messages/ai_summarize`（ids ≤400 或时间窗 ≤300 条），LLM 返回 Markdown 摘要渲染在上下文卡片里；prompt 注明 `[非文本]` 行是图片/表情。
- **通知点击无反应**：Toast 卡片换成 `ToastCardView`（hitTest 让 ✕ 命中按钮、其余命中卡片，mouseUp 位移 <4pt 才算点击）；`AppState.openInMainWindow` 先探测页面 JS 是否就绪，未就绪回退整页加载（`?event=` 由页面 onload 处理）；`handleDeepLink` 去掉 ?event 分支避免双重触发。
- 附带修复：httpx 0.28.1 移除了请求级 `proxies` 参数（`ai_summarize` 首调报 `unexpected keyword argument 'proxies'`），app.py/summarize.py 都改为仅配置代理时才传；`selectChat` 点击左侧会话会退出上下文模式。
- 回归：`check_system` 27/27、`test_e2e` 12/12（keys 列表允许空状态）。

## 17. 供应商重命名 / 备注（2026-10-17 14:5x 反馈）

- 每行操作区加「✎」按钮 → 弹窗可改**显示名称**（≤64）和**备注**（≤128，显示在名称下方）。
- 存储为本地 overlay：`data/provider_aliases.json`（{id: {alias, note}}），**不动** DSH cordis.patch.yml / WorkBuddy models.json / cc-switch.db 这些第三方配置；「恢复原名」一键清掉。
- 有别名时名称旁带「别名」小徽标，副标题追加 `原名「…」`；删除 provider 时自动清掉对应别名记录。
- 后端：`POST /providers/rename`（JSON：id/name/note，两者都空 = 恢复原名，先校验 id 存在）；`providers.py` 新增 `apply_aliases()` / `set_alias()` / `_read_aliases()`。
- 回归：`test_e2e` 12/12。
