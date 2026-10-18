# Telegram / Discord / 飞书话题群接入计划

> 状态：方案阶段，暂不修改运行代码。研究依据：`docs/RESEARCH_TELEGRAM.md`、`docs/RESEARCH_DISCORD.md`、`docs/RESEARCH_FEISHU_TOPIC.md`。
> 目标：把三个平台接入现有“鸡蛋收割机”的采集、媒体、API Key 提取、情报简报和操作中心，而不是只增加一个“AI 总结按钮”。

## 当前现场配置（2026-10-18）

- 用户已在 Telegram API development tools 创建 Active 应用，页面显示 API ID；页面中的 `api_hash` 仍是 secret，不在本计划复述、不保存。运行时应由用户在本机安全输入或 Keychain 读取，不进入聊天/Git/日志。
- Telegram 走 FlClash：用户偏好日本A01｜IEPL，备选俄罗斯A01。
- Discord 走 FlClash 美国节点。
- FlClash 的协议和本机代理端口尚未确认；不得猜默认端口，也无需把节点凭证交给应用。
- 飞书事件订阅尚未配置，用户需要按向导逐步创建自建应用并验证话题群事件。

## 1.1 FlClash 代理配置

1. 在 FlClash 设置中查找 HTTP、SOCKS 或 Mixed Port，并确认本机监听地址/端口；将该值分别填入 Telegram 与 Discord 的来源代理设置。若没有独立本机端口，则使用 FlClash 当前全局/规则代理模式。
2. 在 FlClash 节点列表将 Telegram 流量切到日本A01｜IEPL，应用内执行 Telegram 专用连通性测试；失败时切换俄罗斯A01后重测。
3. 将 Discord 流量切到美国节点，再执行 Discord REST 专用代理测试。
4. 节点名称只代表用户选择，不保证服务端区域或连通性。测试仅显示目标域名、连通状态、延迟和错误类别，不打印账号、验证码、token、api_hash 或代理认证信息。
5. 若应用与 FlClash 在不同网络命名空间/容器中，`127.0.0.1` 可能不指向宿主机；本项目原生 macOS 场景先使用本机监听地址，容器场景另行指定宿主机可达地址。


### Telegram

采用 **Telegram User Client**，不是 Bot API。原因是需要用户本人登录、读取已加入群/频道的历史、监听实时更新、支持代理、断线恢复和按日期补采。

#### 用户端登录与配置流程

1. 用户在 [my.telegram.org](https://my.telegram.org) 登录自己的 Telegram 账号，进入 API development tools 创建 `api_id` 与 `api_hash`；这两个值属于应用配置，不等同于登录密码。
2. 在本系统“Telegram 用户账号”面板填写 `api_id`、`api_hash`、手机号和代理（可选），点击“发送验证码”。验证码由 Telegram 提供，可能发送到已登录的 Telegram 客户端或其他官方渠道，不承诺一定是短信；验证码只在用户输入框中短暂存在，不写日志。
3. 用户填写 Telegram 提供的登录验证码；仅当账号启用两步验证且客户端返回 `SessionPasswordNeededError` 时，再填写 2FA cloud password。验证码和 2FA 密码都不持久化。
4. 授权成功后仅保存加密的 Telegram session（授权密钥/实体缓存），不保存明文验证码或 2FA 密码。显示账号昵称、手机号掩码和连接状态。
5. 授权后枚举该用户自己可见的群/频道，由用户勾选采集范围；不得自动加入群或绕过群权限。
6. 支持“断开连接、删除本地 session、登出当前授权、重新登录、测试代理、立即历史回填”。删除本地 session 只会使本机失去可复用授权，不等于撤销 Telegram 服务器端会话；如需撤销，应调用 User Client logout，并引导用户在 Telegram Settings → Devices 终止其他会话。

代理配置支持 `socks5h://`、`socks5://`、SOCKS4（具体客户端版本能力需实测），HTTP(S) 作为兼容项。代理测试、Flood Wait、验证码失败、2FA 失败和 session 失效必须显示为可操作状态。

第一版不做：自动加入群、绕过权限读取私密群、批量账号、代替用户执行群内操作。
参考项目取舍：

- `tgtldr`：采用其“账号运行时 + 实时 updates + 历史 backfill + 媒体队列 + 摘要任务”的边界。
- `AlphaRadar`：采用 Telethon 的登录、`iter_messages`、`NewMessage`、关键词告警思路，但补足媒体和凭证安全。
- `TelegramMessagesSummarizer`：只借鉴导出→过滤→总结→回发流程，不采用其一次性批处理作为核心架构。

### Discord

按用户端采集需求，采用 **用户本人提供的 Discord user token + REST 轮询/历史同步**，不采用 Bot token/OAuth2 作为默认登录方式。Discord 本身没有面向普通用户的官方“输入账号密码换取 token”流程；系统只能接收用户本人在受信任环境中主动配置的 token，不能代为收集密码、验证码、Cookie，也不能扫描浏览器或 LevelDB 自动提取 token。

#### 用户端登录与配置流程

1. 用户在本机已登录的 Discord 客户端/浏览器中，通过用户本人明确操作取得自己的授权 token；系统不提供密码登录、验证码代填、Cookie 抓取或 LevelDB 扫描。
2. 在本系统“Discord 用户账号”面板粘贴 token，输入专用 HTTP/HTTPS 代理（可选），点击“测试连接”。token 输入框应遮罩，提交后只保存加密凭证，列表只显示掩码和用户/账号探测结果。
3. 测试连接调用 Discord REST 的当前用户/可见服务器接口，验证 401/403/429，并展示 token 具备的可见范围；不把 token 写入 URL、日志、错误栈或普通消息表。
4. 枚举用户可见服务器、频道、Forum 和 Thread，用户手动勾选采集范围。历史同步使用 `/channels/{channel_id}/messages` 的 `before/after` 分页；实时能力采用定时 REST 增量轮询，不能声称拥有 Bot Gateway 事件。
5. 支持代理测试、暂停/恢复、历史回填、删除 token、重新配置和断点游标；删除凭证后立即停止轮询并清除本地密文。

必须明确：用户 token 自动化可能违反 Discord 条款并导致账号限制/封禁；仅支持用户自有账号、明确授权和最小采集范围。不得实现 token 窃取、批量账号、绕过服务器权限或冒充用户发言。若未来需要合规实时 Gateway，应另行评估官方 Bot/OAuth2 方案，但不替代本用户端方案。

必须支持：

- 用户 token 加密保存、遮罩和可撤销删除
- 用户可见服务器、频道、Forum、公开/私有 Thread 枚举
- REST 历史分页和断点补偿
- Discord HTTP/HTTPS 代理；代理连通性测试和 429/Retry-After 退避
- 附件、embed、回复关系和消息引用的原始字段保存

### 飞书

用户目标是**话题群**，因此采用 **飞书企业自建应用 + 事件订阅 + 服务端 API**，不是自定义机器人 Webhook，也不使用个人账号 Cookie/抓包自动化。

话题不是独立 chat_id，而是群聊内的 Thread。必须保留：

- `chat_id`
- `message_id`
- `thread_id`
- `root_id`
- `parent_id`
- `create_time`
- 原始事件 JSON

必须支持：

- `im.message.receive_v1` 实时事件
- URL challenge、Verification Token、Encrypt Key、HTTPS
- 事件幂等和快速 200 响应
- 群历史分页回填
- 本地按 `thread_id/root_id/parent_id` 重建话题
- 官方回复接口发送话题内回复
- 图片/文件权限验证和异步下载

用户尚未配置飞书事件订阅时，先按 [`飞书话题群配置向导`](WIZARD_FEISHU_TOPIC_SETUP.md) 从创建企业自建应用开始；在完成 HTTPS 回调和测试群验证前，不视为飞书来源已就绪。

自定义机器人 Webhook 只作为未来的简报发送出口，不作为采集入口。落地前要在目标租户测试普通消息、话题根消息、话题回复、图片、文件、撤回和编辑，因为不同租户权限与事件字段需要实测。

## 2. 统一领域模型

当前 `raw_messages` 已以 `platform + chat_id + msg_key` 做幂等基础。接入前需扩展，而不是把平台字段硬塞进 `content`：

```text
source_accounts
- id, platform, display_name, auth_type
- status, last_seen_at, config_encrypted

raw_messages 增加
- source_account_id
- remote_message_id
- thread_id, root_id, parent_id
- reply_to_id
- raw_payload
- sync_cursor / edit_version（按实现需要）
```

统一消息映射：

```text
NormalizedMessage
- platform / source_account_id
- chat_id / chat_name
- remote_message_id / msg_key
- sender_id / sender_name
- ts / edited / recalled
- text / caption / links
- thread_id / root_id / parent_id / reply_to_id
- attachments / raw_payload
```

唯一键建议为：

```text
(platform, source_account_id, chat_id, remote_message_id)
```

不能把 Telegram message ID、Discord Snowflake、飞书 message ID 直接当本地 SQLite 主键。所有连接器都必须满足：重试幂等、断线补偿、先落盘后处理。

## 2.1 QQ 汇总型群消息

除 Telegram/Discord/飞书外，现有 QQ 来源还需支持群内转发的多群日报型 KEY 汇总：按 `【第N组】` 切分、提取自定义 Base URL/模型/备注、解码 Base64 Key、区分来源声称失效与实际 probe 失败，并允许识别预览后保存未验证候选。具体解析和验收规则见 [`QQ 群聊 KEY 汇总解析方案`](PLAN_QQ_GROUP_KEY_DIGEST.md)。手工输入必须接受自定义 endpoint，不得默认为官方 OpenAI endpoint。

## 3. 连接器生命周期

每个来源统一采用以下状态机：

```text
未配置 → 授权中 → 已连接 → 同步中 → 监听中
                         ↘ 补采中
已连接/监听中 → 暂停 → 重连中 → 错误/需重新授权
```

每个连接器提供：

- `connect()` / `disconnect()`
- `health()`
- `list_chats()` / `select_scope()`
- `backfill(from, to)`
- `start_realtime()`
- `pause()` / `resume()`
- `delete_credentials()` / `delete_source_data()`

统一记录：最后事件时间、最后成功同步时间、游标、重连次数、限流等待、最近错误（脱敏）。

## 1.1 当前现场配置（2026-10-18）

- 用户已在 Telegram API development tools 创建 API app，并提供了 `api_id`；`api_hash` 已在聊天中以遮挡形式展示，本报告和配置文件不复述、不保存该 secret。不要把该值复制到聊天、Git、普通 `.env` 明文或日志。
- Telegram 出站代理使用用户本机 FlClash：优先尝试 **日本A01｜IEPL**，备选 **俄罗斯A01**。
- Discord 出站代理使用 FlClash **美国节点**。
- 飞书事件订阅目前尚未配置；用户需要一步一步引导建立企业自建应用并配置事件回调。
- FlClash 的具体 HTTP/SOCKS 监听端口和混合端口尚未确认；接入前须在 FlClash 设置页面核对，不猜默认值。推荐在应用内分平台填写 `127.0.0.1:<FlClash 本机监听端口>`，并支持 Telegram/Discord 分别选择节点/代理配置；若 FlClash 仅暴露一个本地代理端口，则节点选择在 FlClash 客户端完成，应用只配置本地代理地址。

### Phase 0：平台账号与测试条件

1. Telegram 使用用户已建立的 API app；登录时输入手机号、Telegram login code 和按需的 2FA。
2. 在 FlClash 确认本机代理端口；Telegram 用日本A01｜IEPL（备选俄罗斯A01），Discord 用美国节点。
3. Discord 由用户在本机安全配置本人已有 user token；报告与助手不索取、不回显 token。
4. 飞书创建企业自建应用、申请消息权限、配置事件订阅 URL/Encrypt Key，并用测试话题群验证事件；部署回调需要 HTTPS 可达入口。
5. 明确每个平台只采集哪些群/频道/话题，默认不全量采集。
6. 凭证只进系统 Keychain 或加密配置，不记录 Token/Secret/session。

验收：三个平台都能完成“配置/授权/状态检查”，且失败信息可操作、无敏感信息泄露。

### FlClash 代理操作说明

1. 在 FlClash 设置中查找本地代理端口（HTTP、SOCKS 或 Mixed Port）；记录协议和端口，不需要把节点密码交给应用。
2. 在 FlClash 节点列表选择 Telegram 节点：先测试“日本A01｜IEPL”；连接失败、延迟高或 Telegram RPC 不通时切换“俄罗斯A01”。
3. 在 Discord 设置中选择美国节点。若 FlClash 是全局/规则代理而非独立端口，节点选择由 FlClash 管理，采集器只需使用该本地代理端口。
4. 对每个平台执行连通性测试，输出只包含目标域名、连接状态、延迟和错误类型，不记录账号、验证码、token、api_hash 或代理认证信息。
5. FlClash 的端口和模式因用户设置可能变化，必须从本机设置页确认；不能只凭节点名称推断代理类型或端口。

### Phase 1：统一来源与存储层

1. 新增来源账户配置和加密凭证存储。
2. 扩展消息表的远端 ID、线程关系、原始 payload、编辑/撤回字段。
3. 抽象 `SourceConnector` 接口。
4. 增加媒体资产表/下载任务表，设置大小、类型和超时限制。
5. 为每个平台写 fixture，不依赖真实账号跑单元测试。

验收：同一事件重复投递只产生一条记录；不同平台相同远端 ID 不冲突；线程关系不丢失。

### Phase 2：Telegram User Client

推荐 Python Telethon（与现有 FastAPI/Python 体系一致），而非把 Go `gotd/td` 单独引入主进程。

1. 实现代理配置和连通性测试。
2. 实现手机号/验证码/2FA 向导，session 加密保存。
3. 实现已加入群/频道枚举与手动选择。
4. 实现历史按日期回填、游标、Flood Wait、断线重连。
5. 实现实时事件监听和重启恢复。
6. 保存文本、caption、链接、回复关系和媒体元数据。
7. 媒体异步下载；先支持图片/文件，音视频转录和视觉理解后置。
8. 复用现有 API Key 提取、NSFW 检测、情报简报和 Toast。

验收：断网恢复后无重复、无明显漏消息；历史和实时消息进入同一情报流；代理切换不需要重新设计登录模型。

### Phase 3：Discord 用户端 Token

1. 设计用户 token 的安全粘贴、加密保存、掩码展示、删除和重新配置流程。
2. 实现 REST 当前用户/可见服务器探测、401/403/429 错误翻译和代理连通性测试。
3. 实现历史分页、`before/after` 游标、断点恢复和重复消息幂等。
4. 枚举服务器、频道、论坛和线程，并允许用户选择范围。
5. 保存附件/embed/message reference；媒体下载进入统一队列。
6. 定时 REST 增量轮询；成功落盘后才推进同步指针。
7. 封装代理、429、Retry-After 和全局限流；明确标注“非 Gateway 实时事件”。
8. 严禁密码登录、验证码代填、浏览器 token 扫描、批量账号和自动发言。

验收：用户可在不提供密码/Cookie 的前提下配置本人 token；历史分页可断点恢复；线程和父频道视图都能正确展示；删除 token 后立即停止同步；UI 不声称提供 Bot Gateway 实时能力。

### Phase 4：飞书话题群

1. 完成企业自建应用权限申请和事件订阅。
2. 实现 challenge、Encrypt Key 解密、鉴权、幂等和异步队列。
3. 实现群历史分页和话题关系本地重建。
4. 用测试群验证根消息、话题回复、普通消息和跨页排序。
5. 实现图片/文件资源权限验证与异步落地。
6. 实现官方 reply API 的话题内回复。
7. 可选增加飞书机器人 Webhook 作为简报发送出口。

验收：能在指定话题群内实时采集并按 Topic 聚合；重复回调不重复入库；回复不会误发到普通群消息流；权限不足被明确标记为不可回填。

### Phase 5：统一 UI 与 AI 工作流

1. 新增“信息来源”面板：账号、状态、代理、范围、最近同步、错误。
2. 消息流按平台筛选，并按群/频道/话题筛选。
3. 对话上下文展示线程树，而不是只展示线性消息。
4. AI 摘要保留来源消息链接、平台、线程和时间。
5. API Key 提取、链接情报、媒体 NSFW、下载审批复用现有流程。
6. 增加来源级暂停、删除凭证、删除数据和导出功能。

## 5. AI 不应成为采集器的一部分

采集和总结必须解耦：

```text
平台连接器 → 标准化入库 → 媒体/链接处理 → 规则提取
                                      ↓
                               AI 问答/定时简报
```

这样可以做到：

- 先完整保存消息，再决定是否送 AI
- 更换模型不会重新登录平台
- AI 失败不会丢失同步游标
- 可以只对指定 Topic/频道总结
- URL 只作为数据，不让模型自动执行

## 6. 主要风险与硬限制

- Telegram session 等同账号控制权，必须加密、可撤销、不可进日志。
- Telegram Flood Wait 和代理断连必须成为显式状态，不得无限重试。
- Discord user token 等同账号控制权，属于非官方 self-bot 风险方案；只允许用户主动提供本人 token，禁止获取、扫描、购买或代填他人 token，并明确可能被限制/封禁。
- 飞书能否读取全部历史受租户权限、应用入群和群策略限制，不能承诺“所有 Topic 都能回填”。
- 媒体下载必须限大小、限类型、做 NSFW/恶意文件处理，不能因为 AI 摘要直接全量下载。
- 三个平台都需要保留原始远端 ID 和原始 payload，避免未来字段变化导致无法追溯。

## 7. 推荐决策

推荐实现顺序：

1. **Telegram User Client**：最先做，采用用户已创建的 Telegram API app；代理首选 FlClash 日本A01｜IEPL，备选俄罗斯A01，验证手机号/Telegram login code/2FA、session、历史、实时、媒体和断线恢复。
2. **Discord 用户端 Token**：随后做，用户本人主动配置 token，Discord 走 FlClash 美国节点；验证 REST 历史/增量、代理、线程/附件，不承诺 Gateway 实时事件。
3. **飞书企业自建应用**：最后做，先按 [`飞书话题群配置向导`](WIZARD_FEISHU_TOPIC_SETUP.md) 完成租户配置，再以测试群验证事件字段、权限和 Topic。

认证路线明确为：**Telegram 使用用户手机号登录生成 User Client session；Discord 使用用户本人主动提供的 user token；飞书使用企业自建应用的 app_id/app_secret 与事件订阅。** 当前不建议直接开工写某个平台的采集代码。下一步应先完成 Phase 0 的账号、代理、权限和测试群确认，再根据真实条件冻结数据模型和连接器接口。

## 9. 一手官方依据

### Telegram

- [Telegram API Authorization](https://core.telegram.org/api/auth)
- [Telethon Signing In](https://docs.telethon.dev/en/stable/basic/signing-in.html)
- [Telethon Sessions](https://docs.telethon.dev/en/stable/concepts/sessions.html)
- [Telethon Proxy](https://docs.telethon.dev/en/stable/concepts/proxy.html)
- [Telethon Client Reference（历史、下载、连接）](https://docs.telethon.dev/en/stable/quick-references/client-reference.html)
- [Telethon Events Reference](https://docs.telethon.dev/en/stable/quick-references/events-reference.html)

### Discord

- [Discord API Documentation](https://discord.com/developers/docs/intro)
- [Get Channel Messages](https://discord.com/developers/docs/resources/message#get-channel-messages)
- [Gateway](https://discord.com/developers/docs/topics/gateway)
- [Gateway Intents](https://discord.com/developers/docs/topics/gateway#gateway-intents)
- [Gateway Events](https://discord.com/developers/docs/topics/gateway-events)
- [Channel Types（含 Forum/Thread）](https://discord.com/developers/docs/resources/channel#channel-object-channel-types)
- [Message Object](https://discord.com/developers/docs/resources/message#message-object)
- [Rate Limits](https://discord.com/developers/docs/topics/rate-limits)
- [OAuth2](https://discord.com/developers/docs/topics/oauth2)

### 飞书

- [话题概述](https://open.feishu.cn/document/im-v1/message/thread-introduction)
- [事件订阅概述](https://open.feishu.cn/document/server-docs/event-subscription-guide/overview)
- [配置请求网址与加密](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/request-url-configuration-case.md)
- [Encrypt Key 加密配置案例](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/encrypt-key-encryption-configuration-case?lang=zh-CN)
- [获取会话历史消息](https://open.feishu.cn/document/server-docs/im-v1/message/list?lang=zh-CN)
- [获取消息详情](https://open.feishu.cn/document/server-docs/im-v1/message/get?lang=zh-CN)
- [回复消息](https://open.feishu.cn/document/server-docs/im-v1/message/reply)
- [自定义机器人使用指南](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot)

以上链接用于核验平台协议、权限、事件、线程和限流约束；具体租户权限、事件字段和回复参数仍需在 Phase 0 测试租户实测。

## 10. 研究报告

- [`docs/RESEARCH_TELEGRAM.md`](RESEARCH_TELEGRAM.md)
- [`docs/RESEARCH_DISCORD.md`](RESEARCH_DISCORD.md)
- [`docs/RESEARCH_FEISHU_TOPIC.md`](RESEARCH_FEISHU_TOPIC.md)
- [`docs/WIZARD_FEISHU_TOPIC_SETUP.md`](WIZARD_FEISHU_TOPIC_SETUP.md)
- [`scripts/setup-feishu-topic.sh`](../scripts/setup-feishu-topic.sh)（运行：`./scripts/setup-feishu-topic.sh`）
- [`docs/PLAN_QQ_GROUP_KEY_DIGEST.md`](PLAN_QQ_GROUP_KEY_DIGEST.md)
