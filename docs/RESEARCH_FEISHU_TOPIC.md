# 飞书话题群（Topic / Thread）接入可行性研究

> 调研日期：2026-10-18。结论以飞书开放平台官方文档为主；开放平台站点部分页面在当前网络环境无法直接抓取，因此对“文档明确支持”的内容与“需实测/需确认”的内容分开记录。

## 结论摘要

- **可接入，但应采用“自建应用 + 事件订阅 + 服务端 API”方案**，不要把自定义机器人 Webhook 当作采集入口。自建应用能订阅消息事件并通过 API 读取/发送消息；自定义机器人主要是单向发送，不能读取群历史或订阅群消息。
- 飞书的“话题群”不是一个独立的普通 `chat_type`；它是群聊中的 Topic/Thread 消息组织方式。群消息对象通过 `thread_id` 等字段表达所属话题，回复时需使用官方“回复消息”接口并正确设置话题回复参数。
- **实时接收可行**：订阅 `im.message.receive_v1`（接收消息）事件，事件 payload 中包含消息主体及其 `message_id`、`root_id`、`parent_id`、`thread_id`（具体字段以当前事件 schema 为准）。应原样保存全部消息 JSON，避免只保存文本而丢失线程关系。
- **历史补采可行性有限且强依赖权限/身份**：使用“获取会话历史消息”接口按 `container_id`（群 chat_id）分页拉取；应用必须具备对应消息读取权限且通常只能看到应用有权访问的会话。话题回复应先按 chat 拉取，再按 `thread_id` 在本地重建线程；不要假定存在一个可无限回溯的独立 thread 历史接口。
- **加密回调可行**：事件订阅支持 Encrypt Key；服务端需按官方算法解密 `encrypt` 字段，并用 `Verification Token`/请求校验完成 URL 验证。生产环境建议启用加密、HTTPS、幂等（按 event_id/message_id）和快速 200 响应。
- **Webhook 有明显限制**：群自定义机器人 Webhook 适合发送通知，不提供历史读取、事件订阅或可靠的 Topic 采集能力；还存在频率限制及安全校验（关键词/IP 白名单/签名等，依配置而定）。
- **个人账号自动化不建议**：用个人账号模拟客户端、抓取 WebSocket/私有接口、保存个人 Cookie 或绕过应用权限，存在账号封禁、数据合规、凭据泄露和接口变更风险；本项目应使用企业管理员授权的自建应用和官方 API。

## 官方资料与逐项核对

### 1. 话题/群聊类型

官方“话题概述”说明了消息话题（Thread）模型及相关服务端 API：

- [话题概述（飞书开放平台）](https://open.feishu.cn/document/im-v1/message/thread-introduction)
- [群组管理 FAQ（飞书开放平台）](https://open.feishu.cn/document/group/faq?lang=zh-CN)
- [创建群（飞书开放平台）](https://open.feishu.cn/document/server-docs/group/chat/create?lang=zh-CN)

实现上应将“群”（`chat_id`）与“话题”（`thread_id`）建模为两层：一条根消息开启话题，后续消息通过 thread/root/parent 关系归属。不要把一个 Topic 当成一个新的 chat_id，也不要仅以时间窗口猜测线程。

**待在目标租户实测的字段兼容性**：不同版本/客户端、普通群与话题群的事件 JSON 可能字段出现空值或组合不同；应记录完整 payload，并用 `thread_id`、`root_id`、`parent_id` 做兼容解析。

### 2. 机器人/自建应用能否读取实时话题消息

官方事件订阅入口及消息事件文档：

- [事件订阅概述](https://open.feishu.cn/document/server-docs/event-subscription-guide/overview)
- [接收事件：配置请求网址与加密](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/request-url-configuration-case.md)
- [加密事件配置示例](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/encrypt-key-encryption-configuration-case?lang=zh-CN)
- [获取会话历史消息](https://open.feishu.cn/document/server-docs/im-v1/message/list?lang=zh-CN)

自建应用需在开发者后台开启事件订阅并订阅 `im.message.receive_v1`。应用能否收到某个群的消息取决于应用安装/入群状态、租户配置、事件权限和群策略；不能把“机器人在群里”推断成“可读取全部历史”。建议先用测试群验证：普通消息、话题根消息、话题内回复、@应用、图片/文件、撤回/编辑（若租户允许）。

### 3. 历史消息读取

- [获取会话历史消息](https://open.feishu.cn/document/server-docs/im-v1/message/list?lang=zh-CN)
- [获取消息详情](https://open.feishu.cn/document/server-docs/im-v1/message/get?lang=zh-CN)

服务端 API 支持按会话分页获取消息，并可再按 message_id 获取详情。实际接入须在应用权限页面申请消息读取相关权限（以后台当前权限名称为准，常见为 `im:message`/只读变体），并由管理员授权。权限不足、应用未加入群、跨租户或受保护群都会导致不可读；这些情况应在设计中作为“不可回填”而不是重试风暴处理。

话题回填建议：

1. 保存 `chat_id`、`message_id`、`create_time`、`thread_id`、`root_id`、`parent_id` 与原始 JSON。
2. 以 `page_token`/`page_size` 分页拉取会话历史，按 `create_time` 和 message_id 幂等去重。
3. 本地按 `thread_id`/root 关系重建 Topic；对缺少 thread 字段的消息保留 `thread_id=null`，不可猜测。
4. 对关键话题使用“获取消息详情”补齐结构化内容和资源引用。

### 4. 回复与 thread 字段

- [回复消息](https://open.feishu.cn/document/server-docs/im-v1/message/reply)
- [话题概述](https://open.feishu.cn/document/im-v1/message/thread-introduction)
- [消息对象说明](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/intro.md?lang=zh-CN)

回复 API 的目标是已有 `message_id`；实现必须区分普通回复与话题内回复，并按接口 schema 传递 `reply_in_thread`（或当前版本对应的同义字段）。收到事件时应保存 `root_id`/`parent_id`/`thread_id`，发送时优先以根消息/目标消息的官方字段构造请求，不能把 `thread_id` 拼进文本或 URL 代替结构化回复。

由于官方页面版本可能更新，落地前应以 API Explorer/当前 OpenAPI schema 确认：

- 话题根消息是否 `root_id` 为空或等于自身；
- 事件中的 `thread_id` 是否对根消息也返回；
- 回复接口的 `reply_in_thread` 默认值及机器人在话题群中的行为。

### 5. 权限与应用形态

权限、可见范围和管理员授权以开放平台后台为准。最低能力清单：

- 事件订阅/接收消息事件；
- 读取会话消息（实时事件与历史 API 分开核验）；
- 读取消息详情及必要的资源（图片、文件如要落盘需另申请资源权限）；
- 发送消息/回复消息（若需要机器人回话）；
- 应用可见范围覆盖目标租户用户/群。

自建应用（tenant app）是推荐形态：凭 `app_id`/`app_secret` 获取 tenant access token，按官方 OAuth/服务端 API 调用。不要使用个人登录态代替应用 token。

### 6. 加密回调与可靠性

官方配置资料：

- [事件订阅概述](https://open.feishu.cn/document/server-docs/event-subscription-guide/overview)
- [配置请求网址](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/request-url-configuration-case.md)
- [Encrypt Key 加密配置案例](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/encrypt-key-encryption-configuration-case?lang=en-US)

回调端点需支持 URL challenge；启用 Encrypt Key 后，收到的 `encrypt` 需按官方算法解密，再处理事件。应使用 HTTPS、校验 token/签名、限制请求体大小、快速返回成功并异步入队；按 `event_id` 或 `message_id` 幂等，防止飞书重试造成重复入库。Encrypt Key 不是应用 Secret，不能写入仓库。

### 7. 自定义机器人 Webhook 限制

- [自定义机器人使用指南](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot)
- [Webhook V2 机器人超出频率限制治理](https://open.feishu.cn/document/faq/breaking-change/webhook-v2-robot-exceeds-frequency-limit-management)

自定义机器人 Webhook 是群内发送入口，不等价于开放平台事件订阅：不能用它读取历史消息，也不能依靠它接收群/Topic 全量消息。官方文档另有频率限制治理；发送端必须处理限流/429、退避和失败重试，并按指南配置安全校验。若需要“监听 + 回填 + 话题内回复”，应使用自建应用，而不是仅配置 Webhook。

## 对当前项目的适配建议（仅建议，未改代码）

当前仓库已有的采集入口主要是 QQ/微信：`app.py` 的 `/onebot`、`qq_ingest.py`、`wechat_watch.py` 与 `store.py`；未发现 Feishu/Lark 接入代码。现有 append-only 原始消息思路适合复用，但不应把 Feishu 的 thread 关系压扁成普通群消息。

建议新增独立适配层（名称仅供设计）：

- `feishu_ingest.py`：challenge/解密/事件校验、消息规范化、幂等入库；
- `feishu_api.py`：tenant token、历史分页、消息详情、回复；
- 字段映射：`platform=feishu`、`chat_id`、`message_id`、`thread_id`、`root_id`、`parent_id`、`message_type`、原始 JSON；
- webhook 路由与现有 `/onebot` 分开，避免 Feishu 重试/鉴权逻辑互相污染；
- 先落盘再做 OCR/LLM/媒体下载，资源 URL 及时按权限下载；
- 测试覆盖：普通群消息、Topic 根消息、Topic 回复、跨页历史、重复事件、加密 challenge、429 重试、撤回/编辑和权限拒绝。

## 风险判断

| 方案 | 读取实时 Topic | 读取历史 | 发送 Topic 回复 | 风险/结论 |
|---|---:|---:|---:|---|
| 自建应用 + 事件订阅/API | 可行（权限/入群前提） | 可行但受权限/可见范围限制 | 可行，按 reply API | **推荐** |
| 群自定义机器人 Webhook | 不提供全量读取 | 不可行 | 可发送，但限频 | 仅作通知出口 |
| 个人账号自动化/抓包 | 非官方、不稳定 | 非官方、不稳定 | 非官方 | **不建议，禁止作为生产方案** |

最终建议：先创建最小权限自建应用，在测试话题群完成一次端到端验证（事件 JSON、历史分页、`thread_id` 关系、加密回调和回复接口），再决定是否接入现有存储；在官方权限或字段验证完成前，不承诺“可读取所有历史 Topic”。
