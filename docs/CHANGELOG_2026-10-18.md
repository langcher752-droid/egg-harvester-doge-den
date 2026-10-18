# 2026-10-18 变更记录：四条体验修复 + 羊毛站数据源

本文件记录 2026-10-18 这一轮改动的根因、实现与验证方式，接手时按目录查即可。

## 一、四条用户反馈的修复

### 1. 「视频 / 公众号文章也被当成鸡蛋」

**根因**：`probe.py` 只判断 HTTP 状态码 200。很多网页（YouTube、B 站、公众号、网盘分享页）本身就返回 200，于是被当成「可用的 API 地址」入库。典型样本：`http://xlai.rf.gd/?k` 返回 `HTTP 200 + <html>...aes.js...`，旧版判为 `ok=True`。

**修复**（`probe.py`、`extract.py`、`pipeline.py`、`app.py`）：

- `probe.py` 新增：
  - `CONTENT_HOSTS`：mp.weixin.qq.com、bilibili.com、youtube.com、x.com、t.me、pan.baidu.com、xiaohongshu.com 等 26 个内容站；`CONTENT_PATH_RE`：`/(s|article|video|watch|share|post|note|status)/`。
  - `is_content_url()` / `is_plausible_api_url()`：接口地址必须是 `http(s)://` + 带点域名，且不是内容页。
  - `_is_json_response()`：`content-type` 含 json，或响应体以 `{` / `[` 开头，且能被 `r.json()` 解析。
  - `probe()`：URL 不可疑 → `/models` 必须 200 + JSON + `data` 列表；失败再试 `/chat/completions`，必须 200 + JSON + `choices`。否则返回明确错误（`接口地址不像 API（疑似视频/文章/分享链接）`、`响应是网页/非 JSON（HTTP 200 但不是 API）`）。
- `extract.py`：URL 与 key 配对时先过 `is_content_url` / `is_plausible_api_url`，内容页在提取阶段直接丢弃（不再浪费一次网络探测）。
- `pipeline.py`：`process_text` 命中内容页时返回 `ok=False` + `疑似视频/文章/分享链接，不是 API 接口`。
- `app.py` `/harvest`：文本分支统计被忽略的链接数，返回 `ignored`；前端手工收割抽屉显示「已忽略 N 条视频 / 公众号文章 / 分享链接」。

**验证**：B 站链接 + 假 key → `{"count":0,"ignored":1}`；`https://mp.weixin.qq.com/s/abc123` → `ok:false`；正常 key `https://seekai.cc/v1` → `ok:true` / 29 模型（无误杀）；垃圾 key（xlai.rf.gd）重测失败并把状态改为 invalid。

### 2. 「没有批量删除无效鸡蛋」

**修复**：

- 后端 `app.py`：`POST /keys/delete_batch`（`key_ids=1,2,3`）、`POST /keys/delete_invalid`（删 `status IS NULL OR status != 'valid'`）。
- 前端鸡蛋库工具栏：`全选`（半选态显示）、`删除选中 (N)`（未选时禁用）、`清除失效`（先查数量再删）。
- 单卡删除保留不变。

**验证**：插入一条临时 invalid key → `POST /keys/delete_invalid` 返回 `{"ok":true,"deleted":1}`；`/keys/delete_batch` 删除 id=9 返回 `{"ok":true,"deleted":1,"requested":1}`。

### 3. 「想要卡片 / 列表两种排版」

**修复**（`templates/index.html`）：

- 工具栏加「卡片 / 列表」切换，状态存 `localStorage['keys-layout']`。
- `renderKeys` 拆成 `keysCardHtml()` / `keysRowHtml()`；列表布局为 6 列栅格（勾选 / 地址 / Key / 来源 / 延迟 / 操作），窄屏堆叠。
- 选择集 `selectedKeyIds`（Set）在两种布局间保持；`syncKeyCheckboxes()` / `updateKeysSelectionUI()` 统一同步。

### 4. 「鸡蛋站筛选点了卡片没变化」

有两个独立根因：

1. **渲染根本没跑**：`templates/index.html` 末尾对 `DogeUI.loading` / `DogeUI.pop` 做增强包装时写成 `const origLoading = DogeUI.loading; ... origLoading(el, enhanced)`，方法内部的 `this` 变成 `window` → `this.path is not a function` 抛错，`loadIntelSummaries()` 第一行就崩，简报永远 0 张卡。修复：`origLoading.call(DogeUI, el, enhanced)`、`origPop.call(DogeUI, mood, ...)`。同样的坑也影响供应商体检、缓存面板。
2. **筛选逻辑是错的**：旧代码 `platform !== 'web' && source === 'welfare'` 会把所有 QQ/微信简报都算作 welfare；baipiao/mjjgo 又没有对应简报。修复：`!intelSource || (platform === 'web' && chat_id === intelSource)`。

配套修复：

- `intel.py` 生成：`HAVING (platform = 'web' AND count(*) >= 1) OR (platform != 'web' AND count(*) > 5)`；web 源在时间窗内没有消息时，取最近 20 条兜底并重算窗口。
- `list_summaries`：每个 `(platform, chat_id)` 只返回最新一份；`chat_name` 用 `COALESCE(groups.name, raw_messages.chat_name)`。
- 前端：`intelLoadSeq` 请求序号防竞态；卡片显示平台徽标（QQ / 微信 / Web · 羊毛站 等）；空态区分「该来源暂无简报」与「还没生成简报」。
- 清理：删除 13 条 `（简报生成失败：...）` 的历史脏简报。

**验证**：`/intel/summaries?limit=60` 返回 20 条（qq/wechat/web 混合）；点 welfare / baipiao / mjjgo / gicp 分别只剩对应 1 张卡；快速连点不串台。

## 二、新增羊毛站数据源（gicp）

见 `交接文档-完整版.md` 5.6。要点：

- `gicp.py`：`GET https://anxmkemjhqwolugoerny.supabase.co/rest/v1/sites?select=*&order=pinned.desc,id.asc&limit=1000`（匿名 key 只读）。
- 落库：`platform=web, chat_id=gicp, msg_key=gicp:<id>`，发送者显示「羊毛站」。
- 挂入 `web_watch.py`（6h 一轮）；`check_system.py` 新增检查项（28→29）。
- 前端：情报流与简报的「Web 来源」都有「羊毛站」chip；`postLink` 支持 `gicp:`。
- 顺手把 web 源的消息发送者统一成来源名，不再显示「匿名」。
- 情报流按 Web 来源筛选改为服务端过滤：`/messages?limit>=500&platform=web&chat_id=<源>`。

**验证**：59 站入库；`WEB 羊毛站` 卡片流 59 条；`/intel/generate platform=web chat_id=gicp` 生成 11 条消息的简报（站点表格：xiaoshengapi.top / susu.wiki / api.jumengai.net …）。

## 三、验证记录

| 检查 | 结果 |
|---|---|
| `check_system.py` | **29/29 通过** |
| `test_e2e.py` | **12/12 通过** |
| 内联 JS `node --check` | 通过 |
| App 截图：鸡蛋库列表布局 | 3 行，切换/全选/删除按钮正常 |
| App 截图：简报 welfare / gicp | 各 1 张卡，徽标正确 |
| App 截图：情报流 羊毛站 | 59 条，正文完整 |

## 四、踩过的坑

- `DogeUI` 的方法内部用 `this`，包装必须 `orig.call(DogeUI, ...)`。
- 用 `./venv/bin/python` 跑 `py_compile` 会让 App 内置 python3.13 重新编译缓存，后端启动卡 2~3 分钟；统一用 `/Users/rancho/Applications/GroupIntel.app/Contents/MacOS/python3.13`。
- `GroupIntel --js` 只在同时带 `--snapshot` 时执行；`--eval` 不支持 await Promise，需要把诊断写进 `localStorage` 再读。

---

# 补记：鸡蛋站站点库（同日，用户反馈 m00975）

## 需求

「鸡蛋站」不应只是一个外链——要点进去是一个站点集合页：可添加新站点（自动上标题和 logo）、选类型（鸡蛋站 / API 中转 / 论坛）、靠谱程度、备注、AI 总结，卡片形式随时点开；并支持从 Safari 阅读列表、个人收藏导入。

## 实现

- 新增 `stations.py`（约 500 行）+ `stations` 表（`url` 唯一，字段见 5.8）。
- `fetch_meta()`：`httpx` 取页面 → 正则解析 `<title>` / `meta description` / `og:*` / `<link rel=icon>` → favicon 下载到 `static/logos/<md5>.<ext>`（>200B 且 ≤300KB 才存）；`/favicon.ico` 兜底。
- 导入：`import_safari(mode)` 用 `plistlib` 读 `~/Library/Safari/Bookmarks.plist`；`import_chrome()` 读 `Default/Bookmarks` JSON；`seed_from_gicp()` 复用 `gicp.fetch_sites()`；`parse_bookmarks_html()` 解析 Netscape 书签。
- `enrich_missing(limit)`：daemon 线程串行补 logo，`_enrich_state` 暴露 `{running,done,total,failed}`。
- 接口挂在 `app.py`（`/stations` 系列，均 `asyncio.to_thread` 包同步 IO）。
- 前端：`view-stations` 面板 + `stationsCache` 状态 + 卡片渲染 + 添加/编辑弹窗 + 导入弹窗 + 来源下拉筛选。

## 验证

| 项 | 结果 |
|---|---|
| `POST /stations/add` mjjgo | `title=买鸡鸡go`，`logo=/static/logos/aed8e44f36cda79d.png` |
| `POST /stations/summarize` | 7.4s 返回 118 字速评 |
| `POST /stations/import kind=gicp` | `added=59` |
| Safari 阅读列表 | `added=181`（App 有 FDA；直接用 python3.13 跑会 `PermissionError`） |
| 补全 logo | 20 个一轮，19 成功（linux.do 403） |
| `check_system.py` | **33/33** |
| 内联 JS `node --check` | 通过（171 KB） |

## 坑

- 直接 `python3.13 -c "import stations"` 时 `store.init()` 偶发 `database is locked`——后端在写库，重试即可。
- `fetch_meta` 对 403/挑战页只能拿到空 meta，导入不会失败（保留 URL + 名称兜底），需要用户手动补名字。
- Safari 阅读列表条目数量大且杂，导入后建议用「来源」下拉筛选整理。

---

# 补记二：导入可勾选 / 站点列表 / 侧栏计数 / 供应商探测修复（用户 m01236）

## 1. 导入改成「先挑后导」

以前点「导入」直接全量入库（一次可能灌进 181 条阅读列表），现在分两步：

- `POST /stations/import_preview`（`kind=safari_reading|safari_favorites|chrome|gicp`）→ `{ok,total,existing,items:[{url,title,note,exists}]}`，只读不写；
- 上传 HTML 走 `POST /stations/import_file_preview`；
- `POST /stations/import_selected`（JSON `{source, items:[...]}`）→ 只入库勾选的，`import_items()` 里按 URL 去重，已存在的计入 `skipped`。

前端弹窗：来源列表点「选择」→ 候选页（搜索框 + 全选 / 清空 + 逐条 checkbox + 「导入选中 (N)」+ 返回）。
候选 ≤30 条默认全选，>30 条默认不选。

## 2. 鸡蛋站支持列表排版

- 工具栏加 `卡片 | 列表` 切换，状态存 `localStorage['stations-layout']`；
- 列表行 `stationRowHtml()`：logo / 名称+域名 / 类型标 / 靠谱点 / AI 速评摘要 / 打开·速评·编辑·删；
- `applyStationsLayout()` 同步 `#stations-list[data-layout]` 与按钮 active。

## 3. 侧栏计数补全

`updateBadges()` 之前只取 `/keys`、`/intel/summaries`、`/files/list`、`/groups`，所以「供应商体检」「鸡蛋站」两个徽标启动时是空的（markup 里是 0）。现在并发多取 `/providers` 与 `/stations`，两处计数开局即正确。

## 4. 供应商探测修复（误判）

`providers.test_one()` 两个问题：

1. `url = base + ("/models" if base.endswith("/v1") else "/v1/models")` —— base 带尾斜杠时拼成 `.../v1//v1/models`；
2. 直接用 `httpx.get`，默认 `python-httpx/x.y` UA，Cloudflare 后面的中转站直接回 403 HTML，被当成「key 失效」。

改成：

- `_models_url(base)`：先 `rstrip('/')`，已含 `/vN` 版本段只补 `/models`，否则补 `/v1/models`（兼容 `/api/paas/v4` 这种）；
- `_UA_HEADERS`：浏览器 UA + `Accept: application/json`，冠到所有探测请求上。

实测：修复前 42 个 provider 里 7 个「失效」，其中 seekai.cc/v1 是 Cloudflare 403 误判；修复后（用户此时已删掉几个失效的）38 个里 35 通、3 个真失败（cc-switch 本地 15721 端口没起、packy/zhipu 缺 credential）。

## 验证

| 项 | 结果 |
|---|---|
| `POST /stations/import_preview kind=gicp` | `total=59, existing=59`（已全导入） |
| `POST /stations/import_preview kind=chrome` | 273 个候选 |
| `POST /stations/import_selected` | `{added:1, skipped:1, requested:2}`（重复 URL 正确跳过） |
| 站点列表排版 | 快照 `/tmp/s4.png`，chips 不再拉伸 |
| 导入候选弹窗 | 快照 `/tmp/s5.png`，`已选 0 / 候选 273`，按钮随勾选启用 |
| `POST /providers/test` | 38 个 → 35 ok / 3 bad（均为真实不可用） |
| `check_system.py` | **36/36**（新增导入勾选、列表排版、徽标三项） |
| `test_e2e.py` | 12/12 |
