# Telegram 项目研究：登录、同步、监听与 AI 总结

> 研究日期：2026-10-18。结论来自项目 README/源码与 Telegram 客户端官方文档；未修改任何代码。

## 结论摘要

| 项目 | Telegram 客户端/登录 | Session 与代理 | 历史/实时 | 媒体与链接 | AI 总结 |
|---|---|---|---|---|---|
| [Ramen-C/TelegramMessagesSummarizer](https://github.com/Ramen-C/TelegramMessagesSummarizer) | 不直接实现 MTProto；依赖外部 `tdl` CLI。用户先执行 `tdl login`，由 tdl 完成账号登录 | `tdl` 自己管理登录态；项目 `json/config.json` 的 `proxy` 传给导出/转发命令（README 示例 `http://localhost:7890`） | GUI 选择最近 N 条或最近 N 小时，调用 tdl 导出；无事件监听，纯手动批处理 | 过滤器丢弃非文本（贴纸、动图等）；文本中的 URL 交给 Prompt 提取；无媒体下载/理解 | DeepSeek/OpenAI 兼容客户端；将消息拼接后要求提取议题、决策、URL、金句，再用 tdl 上传摘要到目标聊天 |
| [fr0der1c/tgtldr](https://github.com/fr0der1c/tgtldr) | Go `gotd/td` Telegram User Client；网页向导手机号→验证码→2FA（API 路由 `/api/telegram/auth/code`）；可选 Bot Token 只用于摘要投递 | Session 序列化后加密存 PostgreSQL；`TGTLDR_MASTER_KEY` 保护 session/API keys/Bot token；`TGTLDR_TELEGRAM_PROXY_URL` 支持 `socks5://`/`socks5h://`，HTTP(S)_PROXY 给 Go HTTP 出站 | 常驻 updates listener；同步已加入群组；按日期执行 history backfill，支持 flood-wait；消息落库 PostgreSQL | v1 保存文本、URL、回复关系、caption、消息/媒体类型；媒体资产有自动下载策略、大小限制和手动下载 API；明确不做图片/视频理解、语音转写 | OpenAI-compatible；按群 Prompt/时间/过滤规则，日报、快速回顾、多群总览，分块/层级聚合，网页查看或 Bot 推送 |
| [web3claw/AlphaRadar](https://github.com/web3claw/AlphaRadar) | Python Telethon；`client.start()` 触发手机号、验证码、2FA；GUI/CLI 均可 | Telethon `.session` 文件路径由 `TG_SESSION_NAME` 指定（默认 `sessions/alpha_radar`）；源码解析 SOCKS4/5 URL，含用户名密码和 `rdns=True` | `iter_messages(chat_id, limit=N)` 拉历史；`events.NewMessage(chats=...)` + `run_until_disconnected()` 实时监听；SQLite 去重落库 | 只保存 `message.text` 和 `raw_json`；媒体无文本显示 `(media)`，未实现媒体下载/分析；摘要 Prompt 特别标记链接 | OpenAI-compatible DeepSeek/GLM/GPT 等；总结未读消息，支持自定义 Prompt，关键词（airdrop/scam/hack 等）告警，CSV/JSON/Markdown 导出 |

## 本项目现场设置（2026-10-18）

- 用户已在 Telegram API development tools 建立 Active 应用并提供 API ID；报告不记录、不回显 `api_hash`，该 secret 不应再次粘贴至聊天或提交到仓库。
- Telegram 用户端代理使用 FlClash：首选 **日本A01｜IEPL**，备选 **俄罗斯A01**。需由用户在 FlClash 设置中确认本机 HTTP/SOCKS/Mixed Port；应用不假设默认端口。若 FlClash 单一代理端口配合规则/全局节点，则切换节点在 FlClash 完成，应用只配置本机代理地址。
- 每次运行前由用户在 FlClash 选定节点；应用侧可维护 Telegram 专用代理配置和测试状态，但不能凭节点名称断定延迟或 Telegram 可达性。


本项目使用 Telegram User Client，而不是 Bot API，以读取当前用户已加入且有权访问的群/频道历史并接收 updates。

1. 用户在 [my.telegram.org](https://my.telegram.org) 的 API development tools 创建 `api_id`（整数）和 `api_hash`（字符串）。这是应用凭证，不是账号密码；`api_hash` 应作为 secret 处理，不能进日志、Git、前端回显或普通消息表。
2. 在系统的 Telegram 用户账号面板填写 `api_id`、`api_hash`、E.164 格式手机号和可选代理，点击发送登录验证码。Telegram login code 可能发送到已登录的 Telegram 客户端或其他官方渠道，不承诺一定是 SMS。
3. 用户输入 login code；只有当 Telethon 返回 `SessionPasswordNeededError` 时，才请求账号的 2FA cloud password。验证码和 2FA 密码仅保存在本次授权请求的内存中，不持久化。
4. 授权成功后只保存按账号隔离、加密的 Telethon session；session 包含授权密钥、DC 信息和实体缓存，等同账号控制权。显示昵称、掩码手机号和连接状态。
5. 枚举用户自己可见/已加入的群和频道，由用户手动选择采集范围；不自动加群、不绕过权限。
6. 提供代理测试、历史回填、断开、删除本地 session、登出当前授权和重新登录。删除本地 session 只清除本机可复用凭证，不等于撤销服务器端会话；需要撤销时调用 User Client logout，并引导用户到 Telegram Settings → Devices 终止其他会话。

状态机：`未配置 → 授权中 → 已连接 → 同步中/监听中`；错误状态必须区分 login code 错误/过期、2FA 错误、Flood Wait、代理不可达、手机号无效/被限制和 session 失效。代理与 OpenAI/普通 HTTP 出站代理分开配置；Telethon/PySocks 版本支持的 SOCKS/HTTP 形式需实测，常用 `socks5://`、`socks5h://` 的远端 DNS 语义不能跨版本盲目保证。

建议配置字段：`telegram.accounts[].api_id`、`api_hash_encrypted`、`phone_e164_masked`、`session_encrypted`、`proxy.url_encrypted`、`selected_chats`、`status`、`last_connected_at`、`last_sync_at`、`last_error_sanitized`、`reconnect_count`、`sync_cursor`。login code 和 2FA password 不得成为持久字段。


### 一手源码事实

- 项目 README 将流程定义为 Export→Filter→Summarize→Forward：GUI 调用 `tdl` 导出 `tdl-export.json`，过滤文本，再调用 DeepSeek 生成 Markdown，最后把 `summary.txt` 通过 tdl 上传回 Telegram。见 [README](https://github.com/Ramen-C/TelegramMessagesSummarizer/blob/main/README.md)。
- 登录不是项目自己的 Telegram API 实现：README 要求运行 `tdl login`。因此账号手机号、验证码、2FA、session 文件格式由 [iyear/tdl](https://github.com/iyear/tdl) 负责，项目只设置 `tdl_path` 并执行 CLI。
- `config.json` 的 `proxy` 示例为 `http://localhost:7890`；README 明确称为 Telegram 代理配置。项目没有 SOCKS/MTProto 代理解析层。
- `core/main.py` 将默认配置设为 `tdl_path`, `proxy`, `api`, `language`, `generate_wordcloud`，GUI 允许修改 tdl 路径和代理；见 [core/main.py](https://github.com/Ramen-C/TelegramMessagesSummarizer/blob/main/core/main.py)。
- `core/summarizer.py` 将格式化消息用换行连接，OpenAI SDK 指向 `https://api.deepseek.com`、模型 `deepseek-chat`，Prompt 要求提取 2–4 个主题、结论/待办、URL 说明和 1–10 条金句；见 [core/summarizer.py](https://github.com/Ramen-C/TelegramMessagesSummarizer/blob/main/core/summarizer.py)。
- README 明确过滤掉贴纸、动图等非文本，因此该项目不是媒体处理器；链接仅作为聊天文本输入由 LLM 识别。

### 适用性与风险

适合一次性“导出一批→总结→回发”，实现简单；不适合实时摘要、增量游标、断线恢复或媒体分析。核心风险是外部 tdl CLI 的 session/代理行为需单独审计；README 中示例版本还提示 API key 可能硬编码在源码，必须改为环境变量/密钥存储。

## 2. fr0der1c/tgtldr

### 一手源码事实

- [README](https://github.com/fr0der1c/tgtldr/blob/main/README.md) 定义为单用户、自部署、Telegram User Client 的群监听和每日摘要系统；功能包括监听已加入群组、数据库落库、定时摘要、网页查看与可选 Bot 推送。
- [架构文档](https://github.com/fr0der1c/tgtldr/blob/main/docs/ARCHITECTURE.md) 明确 v1 保存文本、链接、回复关系、caption；Telegram Runtime 使用 `gotd/td`，支持用户认证、session 持久化、updates 流和断线恢复。v1 不做语音、图片理解、视频理解。
- README 的代理配置：`TGTLDR_TELEGRAM_PROXY_URL=socks5h://host.docker.internal:7890`；用户客户端的登录、监听、群组同步和历史回补都走该变量，且支持 `socks5://` 与 `socks5h://`。OpenAI/Bot API 使用 `HTTPS_PROXY`/`HTTP_PROXY`。
- `TGTLDR_MASTER_KEY` 用于加密保存 Telegram session、OpenAI key、Bot token；默认随机生成并持久化到 `/var/lib/tgtldr/master.key`。密钥/数据卷丢失会导致 session 无法解密。部署建议仅本机/可信内网，公网需访问密码和反代。
- `/api/history-backfills` 接收 `chatId`, `fromDate`, `toDate`，异步启动历史回补；账号同步接口为 `POST /api/telegram/accounts/{id}/sync`。源码见 [history_backfill.go](https://github.com/fr0der1c/tgtldr/blob/main/app/internal/api/history_backfill.go) 与 [telegram_accounts.go](https://github.com/fr0der1c/tgtldr/blob/main/app/internal/api/telegram_accounts.go)。
- 数据模型/媒体仓库区分 `message_type`, `media_kind`, `caption` 与 `media_assets`；附件有 pending/downloading/succeeded/failed/skipped_oversize 状态、自动下载开关和手动强制下载。源码见 [messages_repo.go](https://github.com/fr0der1c/tgtldr/blob/main/app/internal/store/messages_repo.go) 与 [media_repo.go](https://github.com/fr0der1c/tgtldr/blob/main/app/internal/store/media_repo.go)。
- AI 层使用 OpenAI-compatible endpoint，按群自定义 Prompt、时间、过滤规则与模型；日报支持上下文分块、层级聚合、重试和失败 Bot 重投。见 [README](https://github.com/fr0der1c/tgtldr/blob/main/README.md) 和 [ARCHITECTURE.md](https://github.com/fr0der1c/tgtldr/blob/main/docs/ARCHITECTURE.md)。

### 可复用架构

`Telegram Runtime (session + updates + backfill)` → `PostgreSQL message/media store` → `summary context/chunker/rollup` → `scheduler` → `web/Bot delivery` 是三者中最完整的长期运行架构。建议复用其“账号与群绑定、增量监听和历史回补分离、媒体资产异步下载、摘要任务可重试”的边界。

## 3. web3claw/AlphaRadar

### 一手源码事实

- [README](https://github.com/web3claw/AlphaRadar/blob/main/README.md) 要求 `TG_API_ID`/`TG_API_HASH`（从 [my.telegram.org](https://my.telegram.org) 申请），可选 `TG_PROXY=socks5://127.0.0.1:7890`，session 名默认 `sessions/alpha_radar`。
- [telegram_client.py](https://github.com/web3claw/AlphaRadar/blob/main/src/alpha_radar/telegram_client.py) 创建 `TelegramClient(str(session_path), api_id, api_hash, proxy=...)`；只接受 `socks5`/`socks4`，映射 Telethon proxy dict 并设置 `rdns=True`，支持 URL 用户名/密码。
- [cli.py](https://github.com/web3claw/AlphaRadar/blob/main/src/alpha_radar/cli.py) 的 `login()` 调 `await client.start()`，随后打印当前用户；Telethon 会按需进行手机号、验证码、2FA。`fetch_history()` 使用 `iter_messages()`，保存 sender、text、date、reply_to 和 `message.to_dict()` 到 SQLite。
- 实时 `monitor()` 注册 `@client.on(events.NewMessage(chats=...))`，把新消息写入 SQLite，检测关键词并写入 alerts，最后 `run_until_disconnected()`；见同一 [cli.py](https://github.com/web3claw/AlphaRadar/blob/main/src/alpha_radar/cli.py)。
- [summarizer.py](https://github.com/web3claw/AlphaRadar/blob/main/src/alpha_radar/summarizer.py) 把消息格式化为 `[sender]: text`，无文本媒体替换为 `(media/no text)`，调用 OpenAI-compatible API，默认模型 `DeepSeek-V4-Flash`、`temperature=0.3`、`max_tokens=1000`；支持自定义 Prompt。

### 局限与风险

SQLite + 单共享 Telethon client 适合个人工具，但需处理多进程 session 锁（README FAQ 记录 “database is locked”）。源码只保存 `message.text`/`raw_json`，不调用 `download_media()`，媒体内容不会进入 AI；网络代理仅实现 SOCKS4/5，不接受 HTTP URL。关键词告警是字符串匹配，不能替代安全审查；AI API key 和 Telegram session 都是高敏感凭证。

## 4. 官方 API / Telethon / Pyrogram 依据

- [Telegram API: Authorization](https://core.telegram.org/api/auth)：用户账号通过 API ID/hash 与手机号、验证码完成授权；2FA 可能需要密码。`api_id`/`api_hash` 应从官方开发者页面申请。
- [Telethon Authentication](https://docs.telethon.dev/en/stable/basic/signing-in.html)：`TelegramClient.start()` 支持交互式登录/机器人登录，session 名用于持久化授权；不要把 `.session` 文件泄露。
- [Telethon Client Reference](https://docs.telethon.dev/en/stable/quick-references/client-reference.html)：`iter_messages` 用于历史消息；`download_media` 用于下载媒体；`run_until_disconnected` 维持长连接。
- [Telethon Events Reference](https://docs.telethon.dev/en/stable/quick-references/events-reference.html)：`events.NewMessage` 是新消息事件处理器，可用 `chats` 过滤来源。
- [Telethon Sessions](https://docs.telethon.dev/en/stable/concepts/sessions.html)：session 保存 DC、授权密钥和实体缓存；持久 session 可避免每次重新输入验证码。
- [Telethon Proxy](https://docs.telethon.dev/en/stable/concepts/proxy.html)：支持通过 proxy 参数配置 SOCKS/HTTP 等代理（具体版本以文档为准）；`socks5h` 的 DNS 解析在代理侧，适合容器网络。
- [Pyrogram Client](https://docs.pyrogram.org/api/client)：Pyrogram 同样以 `api_id`/`api_hash` 和 session 名创建客户端，支持 `proxy` 配置。
- [Pyrogram get_chat_history](https://docs.pyrogram.org/api/methods/get_chat_history)：异步/同步迭代指定聊天历史，可作为 Telethon `iter_messages` 的替代设计。
- [Pyrogram download_media](https://docs.pyrogram.org/api/methods/download_media) 与 [消息处理器](https://docs.pyrogram.org/api/decorators)：分别覆盖媒体下载和实时消息 handler。

## 5. 面向可复用实现的建议

1. **账号层**：优先 User Client（不是 Bot API）处理群历史与私有群；手机号验证码/2FA 只在前端输入，session 加密后持久化，增加撤销/删除 session 的运维入口。
2. **连接层**：抽象 `TelegramRuntime`，把 proxy、connect/reconnect、账号→群绑定、updates listener、history backfill 分离；为 flood wait、断线、session lock 做状态机和退避。
3. **消息层**：保留 `chat_id/message_id` 唯一键、sender/date/reply、text/caption、raw payload、media kind；媒体异步队列下载并设置大小上限，先把链接和 caption 纳入摘要，图片/音视频理解作为后续插件。
4. **摘要层**：按群配置 Prompt/时间窗/模型；先过滤与分块，再层级汇总，保留来源消息 ID 和失败重试记录；链接需原样保留并做安全提示，不能让 LLM 执行链接内容。
5. **安全与合规**：Telegram session 等同账号控制权，严禁日志/仓库泄露；API key 用环境变量或加密存储；部署默认 loopback/可信内网；尊重 Telegram 条款、群组隐私和消息数据保留要求。

## 6. 主要一手链接

- [Ramen-C/TelegramMessagesSummarizer README](https://github.com/Ramen-C/TelegramMessagesSummarizer/blob/main/README.md)
- [fr0der1c/tgtldr README](https://github.com/fr0der1c/tgtldr/blob/main/README.md)
- [fr0der1c/tgtldr ARCHITECTURE](https://github.com/fr0der1c/tgtldr/blob/main/docs/ARCHITECTURE.md)
- [web3claw/AlphaRadar README](https://github.com/web3claw/AlphaRadar/blob/main/README.md)
- [Telegram API Authorization](https://core.telegram.org/api/auth)
- [Telethon docs](https://docs.telethon.dev/en/stable/)
- [Pyrogram docs](https://docs.pyrogram.org/)
