# Discord 项目与官方 API 调研

> 调研对象：[`jackwener/discord-cli`](https://github.com/jackwener/discord-cli)、[`FlyCatdev/discord-summary`](https://github.com/FlyCatdev/discord-summary)。代码仓库均按当前默认分支源码检查；官方规范链接见文末。

## 结论速览

| 维度 | discord-cli | discord-summary |
|---|---|---|
| 账号/Token | 读取本地 Discord/浏览器 LevelDB，正则提取 user token；保存到项目 `.env`，请求头直接发送 `Authorization: <token>`。README 明确警告 user-token 自动化可能导致限制/封禁。 | 在本地设置页填写 Discord cookie/token（README）；配置保存到本机 `config.json`，源码实际用 `discordToken` 作为 `Authorization` 请求头。不是 Bot OAuth/Gateway 客户端。 |
| 代理 | 源码 `httpx.AsyncClient` 未配置 proxy，默认不支持专用代理。 | `discordProxy` 支持带端口的 HTTP/HTTPS 代理，单独用于 Discord；拒绝 SOCKS5、用户名/密码 URL、查询/片段；AI 请求强制直连。 |
| 历史消息 | REST `GET /channels/{id}/messages`，每页最多 100，按 `before` 分页；`sync` 用 `after` 增量。可调用 guild message search。 | 同一 REST 端点，最多 100/页；按时间范围和 limit 拉取，首次 `before`，增量 `after`。 |
| 实时事件 | 没有 Gateway/WebSocket；`tail` 是轮询最新消息，默认可持续跟随。 | 没有 Gateway/WebSocket；定时线程（默认 30 秒检查调度器，按小时任务）重复 REST 拉取。 |
| 服务器/频道/线程 | 列出 guilds；`/guilds/{guild}/channels` 只保留 type 0/5/15（文字、公告、论坛），没有 thread 列表/线程专用抓取。 | 用户输入频道 ID；`channel_info` 识别 guild 与类型 0/2/4/5/15，但没有 thread API，也没有服务器枚举流程。 |
| 权限/Intent | REST 使用 token 所拥有的访问权限；不涉及 Gateway intents。 | REST 使用 token 所拥有的访问权限；401/403/404 做错误翻译；不涉及 intents。 |
| 媒体 | 仅把附件文件名和 embed title 拼入文本；不下载、OCR、转录或解析附件内容/URL。 | 仅保留 `content`，总结提示要求汇总记录中的链接/附件/文件名；不下载/解析媒体。 |
| 可复用 | `httpx` async REST 客户端、100 分页、Snowflake 时间转换、SQLite schema/查询/导出、结构化 YAML/JSON CLI。 | 代理隔离与校验、原子 config/state、增量指针提交（总结成功后才推进）、多频道调度、报告/Markdown 知识库。 |

## jackwener/discord-cli 源码要点

- README：[项目 README](https://github.com/jackwener/discord-cli/blob/main/README.md)。安装包为 `kabi-discord-cli`；定位是本地优先 SQLite 同步、搜索、导出与 AI agent 结构化输出。
- [`src/discord_cli/auth.py`](https://github.com/jackwener/discord-cli/blob/main/src/discord_cli/auth.py)：在 macOS/Windows/Linux 的 Discord、Chrome、Brave、Edge 等 Local Storage LevelDB/日志中扫描 token 正则；`save_token_to_env` 写入 `.env` 的 `DISCORD_TOKEN`。这属于用户 token，不是 Bot token。
- [`src/discord_cli/client.py`](https://github.com/jackwener/discord-cli/blob/main/src/discord_cli/client.py)：`get_client()` 用 `httpx.AsyncClient`，headers 的 `Authorization` 为 token；没有 `proxy`/transport 设置。`_get` 处理 429，最多重试 3 次并按 `retry_after`/reset-after 睡眠。
- `fetch_messages(..., limit, after=None, before=None)`：每请求 `min(remaining,100)`；默认/`before` 模式把最旧 ID 作为下一游标，`after` 模式把最新 ID 作为下一游标；最后按 Snowflake ID 升序。该分页逻辑适合复用，但生产实现仍应遵照 API 的 rate-limit headers。
- `_parse_message` 把 `content`、附件 `filename`（`[attachment: ...]`）与 embed title（`[embed: ...]`）拼成文本；原始附件 URL、embed 描述、图片/视频字节均未处理。
- `list_guilds` 调用 `/users/@me/guilds`；`list_channels` 调用 `/guilds/{guild_id}/channels`，只保留 type `{0,5,15}`；未调用 `/channels/{channel_id}/threads` 或 Gateway。
- CLI 的 `dc history/sync/sync-all/tail/search` 对应历史、增量、轮询；本地 SQLite 由 [`src/discord_cli/db.py`](https://github.com/jackwener/discord-cli/blob/main/src/discord_cli/db.py) 提供检索、统计、导出。无实时事件流。

## FlyCatdev/discord-summary 源码要点

- README：[项目 README](https://github.com/FlyCatdev/discord-summary/blob/main/README.md)。Python 标准库后端 + Vue 3，无数据库；本地回环服务，凭据写 `config.json`，状态/报告写 `schedule_state.json` 与 `history/`。
- [`server.py`](https://github.com/FlyCatdev/discord-summary/blob/main/server.py) 的 `discord_get_json` 始终发送 `Authorization: token`。README 表述为从网页版 Discord 获取 cookie/token；代码并未实现 cookie header，而是使用配置的 Authorization 值。
- `http_with_fallback` 构造仅 Discord 使用的 HTTP(S) `ProxyHandler`；`discordProxy` 校验要求 HTTP/HTTPS、主机、显式端口且无账号密码/query/fragment/path，故不支持 SOCKS5。LLM 的 `_no_proxy_opener` 强制直连，不读取环境/系统代理。
- `fetch_messages` 按每页最多 100 拉取；首次按 `before` 从最新向旧消息走，增量按 `after` 走；按时间 cutoff 截止。每条只映射 id、timestamp、作者、content，附件/embeds 不进入 transcript。
- `_summarize_one_channel` 读取 `lastMsgId`，成功生成总结并保存报告后才更新状态指针；支持多频道与定时调度，但这是 REST 轮询，不是实时 Gateway 事件。
- `channel_info` 调用 `/channels/{id}` 与 `/guilds/{guild_id}`，识别文字、语音、分类、公告、论坛类型；没有获取 thread 列表或线程消息的专用逻辑。权限错误明确映射 401/403/404。
- 总结 prompt 要求汇总链接、附件、文件名，但实现只传 Discord message `content`，没有媒体下载、OCR、语音/视频转录；远程图片在知识库阅读时仅作为链接（README 隐私说明）。

## 官方 Discord API/Gateway 约束（实现时应遵循）

- [Discord API Documentation](https://discord.com/developers/docs/intro)：官方 API 总入口。
- [Get Channel Messages](https://discord.com/developers/docs/resources/message#get-channel-messages)：频道历史消息 REST 端点；`limit` 范围 1–100，使用 `around`、`before`、`after` 游标。访问结果受账号/应用对频道的权限限制。
- [Gateway](https://discord.com/developers/docs/topics/gateway)：实时连接需 WebSocket Gateway、identify、心跳与 resume；上述两个项目均未实现 Gateway。
- [Gateway Intents](https://discord.com/developers/docs/topics/gateway#gateway-intents)：Gateway dispatch 由 intents 控制；`MESSAGE_CONTENT` 是 privileged intent，Bot 需要在 Developer Portal 启用并在 identify 中请求，且可能受规模/审核限制。REST 历史拉取本身不等于订阅 Gateway intents。
- [Gateway Events](https://discord.com/developers/docs/topics/gateway-events)：实时消息应消费 `MESSAGE_CREATE` 等 dispatch；轮询不能提供事件语义、低延迟或可靠序列保证。
- [Channel Object / Channel Types](https://discord.com/developers/docs/resources/channel#channel-object-channel-types)：区分 guild text、announcement、forum、public/private thread 等类型；若要完整支持线程，应按官方 thread endpoints 与权限模型单独枚举/抓取，而不能只把父频道当作全部消息。
- [Message Object](https://discord.com/developers/docs/resources/message#message-object)：消息的 attachments、embeds、components、message_reference 等结构独立于 `content`；媒体处理必须读取附件 URL/content type/size 等字段，并考虑权限、过期 URL、隐私与下载安全。
- [Rate Limits](https://discord.com/developers/docs/topics/rate-limits)：应使用响应中的限流字段/Retry-After；不要依赖固定 sleep。两项目均做了基础 429 重试，但仍应封装全局/路由桶和跨进程限流。
- [OAuth2](https://discord.com/developers/docs/topics/oauth2)：合规 Bot/用户授权应使用 OAuth2 scopes 与 bot permissions；本调研对象的 user-token 自动化方案存在平台规则和账号风险，不能作为通用 Bot 集成的默认方案。

## 用户端 Token 配置（按本项目需求）

本项目若必须读取用户本人可见的服务器/频道，而无法要求服务器安装 Bot，则采用“用户本人主动提供已有 user token + Discord REST”的实验性方案。这里的 user token 不是 Bot token，也不是公开 OAuth 登录凭据；Discord 没有供第三方应用安全代办的普通用户密码登录换 token 流程。

### 配置步骤

1. 用户确认账号归自己所有，并确认目标服务器/频道的访问权限。
2. 用户从自己的本地 secret manager、环境变量或受信任的交互式隐藏输入提供已有 token。系统不提供密码登录、验证码代填、Cookie 抓取、浏览器 DevTools 操作、LevelDB 扫描、第三方 token 生成/购买或代取他人 token 的功能。
3. 启动时将 token 仅注入内存，先调用 Discord 当前用户/指定资源 REST 接口验证；401、403、404、429 分别显示为凭证失效、无权访问、资源不存在和限流。
4. token 输入必须遮罩；配置文件若必须落盘，应加密并设为仅用户可读（例如 Unix `0600`），绝不写入 URL、普通消息表、请求日志、错误栈或截图。界面只显示掩码和账号探测结果。
5. 用户输入可选 HTTP/HTTPS 代理。代理只用于 Discord REST，校验 scheme、host、显式 port；默认拒绝 SOCKS、代理 URL 内嵌用户名密码、query/fragment，并保持 TLS 端到端。
6. 验证成功后枚举用户可见服务器/频道/Forum/Thread，由用户手动勾选采集范围；使用 REST `before/after/around` 分页历史和增量轮询，不宣称 Gateway 实时事件。
7. 提供暂停、恢复、删除 token、重新配置和断点同步游标。删除本地密文不会撤销 Discord 侧会话；发现泄露后须由用户在 Discord 侧轮换/撤销凭证，再在本系统重新配置。

### 必须明确的风险

Discord 官方政策不支持/禁止以 user token 自动化普通用户账号（self-bot/user-bot）的生产使用，可能导致账号限制或封禁；该方案仅在用户明确要求、仅限自有账号、只读采集和用户自担风险的前提下讨论，标记为实验性、非官方支持。不得用 user token 连接 Gateway、模拟发言或绕过服务器权限。合规替代仍是 Bot token + OAuth2 + 最小 permissions，但不作为本项目用户端默认路线。
### 本项目现场设置（2026-10-18）

- 用户指定 Discord 使用 FlClash **美国节点**。需在 FlClash 设置中核实本机 HTTP/HTTPS 监听端口；应用端配置 `127.0.0.1:<实际端口>`，不猜默认值。若 FlClash 是规则/全局代理，节点选择在 FlClash 客户端完成，采集应用使用本机代理端口。
- 本报告不记录、不请求、不回显用户 token；仅接受用户本人主动提供的已有 token。
- 官方依据：[Discord Developer Policy](https://github.com/discord/discord-api-docs/blob/main/docs/policies_and_agreements/Developer_Policy.md)、[OAuth2](https://discord.com/developers/docs/topics/oauth2)、[Discord Terms](https://discord.com/terms)。实施前需复核当前政策，不能将 user-token 自动化称为官方支持 API。

## 复用建议与风险

1. user token 仅作为用户明确要求的实验性只读方案；存在封禁风险，合规替代仍是 Bot/OAuth2。
2. 可复用 `discord-cli` 的 Snowflake 分页、SQLite 搜索和导出；复用 `discord-summary` 的代理校验、配置脱敏、原子写入和成功后推进游标。
3. 历史同步严格遵循用户可见权限和 REST 限流；不绕过 401/403。发现 token 泄露后由用户在 Discord 侧撤销/轮换。
4. 研究对象缺少完整 Gateway、线程和媒体能力；若未来做官方 Bot 方案，再补 intents、线程枚举、附件安全和 OCR/ASR。
