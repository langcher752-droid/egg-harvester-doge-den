# 飞书话题群接入：人工配置向导

> 可运行交互向导：[`scripts/setup-feishu-topic.sh`](../scripts/setup-feishu-topic.sh)，执行 `./scripts/setup-feishu-topic.sh`。该脚本不采集、不保存任何 Secret，只逐步提示控制台配置。
>
> 目标：从零创建飞书企业自建应用，让它订阅用户指定话题群的消息，并把事件安全送到本机“鸡蛋收割机”。本指南不要求用户把 App Secret、Encrypt Key 或 Verification Token 发给助手。
>
> **重要限制**：飞书事件订阅不是“无需任何权限”。应用需要所在租户管理员批准权限、设置应用可见范围，并按飞书要求配置 HTTPS 可达的事件回调 URL。可以不要求群主逐个给个人账号开权限，但应用能否入群/读取目标会话受租户策略影响。必须在目标租户测试群验证。

## 前置条件

- 一个可创建企业自建应用的飞书开发者/管理员账号。
- 有权限把应用添加到目标话题群（或管理员按租户策略完成安装/可见范围配置）。
- 一条公网 HTTPS 回调地址，能转发到本机/服务器服务。飞书开放平台的事件订阅服务端回调需要公网可达；单纯 `127.0.0.1` 无法接收飞书云端事件。可用自有服务器反向代理或受信任的隧道服务；隧道凭证也应保密。
- 操作前确认采集范围和数据保留策略，仅选择明确授权的群/话题。

## 阶段 1：创建应用

1. 打开[飞书开放平台](https://open.feishu.cn/app)，进入开发者后台。
2. 选择“创建企业自建应用”，填写应用名称、描述和图标，创建后进入应用详情。
3. 在“凭证与基础信息”页面获取 `App ID`、`App Secret`。两者只保存到本机安全配置（推荐 macOS Keychain）；不要粘贴到聊天、截图或 Git 文件。
4. 在“添加应用能力”中添加机器人能力。菜单名称可能随开放平台更新，以控制台实际显示为准。

## 阶段 2：申请权限并发布

1. 进入“权限管理”，申请接收群消息事件和读取会话历史/消息详情所需权限。权限名称以当前开放平台控制台为准，按最小权限原则申请。
2. 如需下载图片/文件，再申请读取消息资源所需的权限；如不需要机器人回话，不申请发送消息权限。
3. 在“版本管理与发布”创建版本并提交发布；按租户流程由管理员审批。
4. 配置应用可用范围，确保测试账号和目标群成员包含在应用可见范围。
5. 只有当应用已发布/审批通过，权限才可按该租户策略生效。错误返回权限不足时，不应通过个人 Cookie 或非官方接口绕过。

## 阶段 3：配置事件订阅

1. 准备 HTTPS 回调地址，例如 `https://<你的域名>/feishu/events`。该 URL 必须由公网可达，并已部署对应的事件回调处理端点；开发环境可通过受信任的隧道临时转发，但不要在无人维护的隧道上长期运行。
2. 进入“事件与回调”或“事件订阅”设置，启用事件订阅，选择长连接接收或将请求 URL 配置为 HTTPS 回调。当前计划的服务端实现采用请求 URL 回调；若用户希望本地长连接，需在实现时重新评估官方 SDK/长连接方案。
3. 设置 Verification Token 与 Encrypt Key，保存到 Keychain/加密配置，不记录日志。
4. 配置 Request URL 后，开放平台会发送 URL challenge。服务器必须按官方格式快速返回 challenge；只有校验成功后才继续。
5. 添加事件 `im.message.receive_v1`（接收消息），保存配置并发布新版本/重新审批（若控制台要求）。
6. 回调必须验证 Token/签名，按配置解密 `encrypt` 字段，限制 body 大小，按 `event_id`/`message_id` 幂等；先快速返回成功，再异步入库/处理。

## 阶段 4：把应用加入测试话题群

1. 在飞书客户端打开目标话题群的群设置/群机器人或应用管理入口，将已发布应用添加到该群；若入口不可见，联系租户管理员检查应用可见范围和群策略。
2. 不要假设“应用无需任何群权限即可收到消息”。在测试话题群分别发送：普通群消息、话题根消息、话题回复、@应用、图片、文件，并检查是否收到事件。
3. 检查事件消息 JSON 中 `message_id`、`chat_id`、`thread_id`、`root_id`、`parent_id` 是否存在；字段空值必须按实际 payload 兼容，不能按时间猜线程归属。
4. 历史回填通过官方“获取会话历史消息”分页接口完成，必须另外确认应用具有读取历史权限；实时事件成功不代表历史消息可读。
5. 若需要在话题内回复，再申请发送消息权限，调用官方回复 API，并验证 `reply_in_thread` 等当前 schema 字段；不需要回复则不申请。

## 阶段 5：配置到本机应用

在“信息来源 → 添加飞书应用”页面输入：

| 字段 | 来源 | 保密要求 |
|---|---|---|
| App ID | 开放平台“凭证与基础信息” | 私有配置，不显示完整值 |
| App Secret | 同上 | Secret，只进 Keychain/加密存储 |
| Verification Token | 事件订阅设置 | Secret，只进 Keychain/加密存储 |
| Encrypt Key | 事件订阅设置 | Secret，只进 Keychain/加密存储 |
| HTTPS Callback URL | 本机部署/隧道地址 | 可显示，但不要含凭证参数 |
| Tenant/应用名称 | 管理后台 | 可显示 |
| 采集群/话题范围 | 用户选择 | 按最小范围配置 |

点击“测试回调”后，界面只显示 challenge 验证、解密、事件权限、最近事件时间和错误类型；日志不得打印任何 Secret 或消息正文全文。

## 阶段 6：验收和排错

- **URL challenge 失败**：确认公网 HTTPS、路径、证书链、challenge JSON 格式及应用进程可达。
- **收不到消息事件**：确认事件订阅已发布、`im.message.receive_v1` 已添加、应用可见范围覆盖用户/群、应用已进入群、管理员审批已完成。
- **收到实时但无法补历史**：实时事件和历史 API 权限分别检查；确认目标会话可见范围和分页游标。
- **普通消息可见但话题字段为空**：导出脱敏后的事件 schema 在测试群核对；不要假定旧事件结构与新客户端一致。
- **图片/文件缺失**：检查资源读取权限、消息资源 API 和资源链接有效期；不要记录长期可用的敏感下载 URL。
- **429/重复事件**：遵循 Retry-After/退避，并按 event_id/message_id 幂等。

完成条件：测试话题根消息与至少一条回复均正确入库，`chat_id` 与 `thread_id/root_id/parent_id` 关系可恢复；重复事件不重复入库；无 Secret 泄露；历史权限结果有明确记录。

## 官方文档

- [飞书开放平台](https://open.feishu.cn/app)
- [事件订阅概述](https://open.feishu.cn/document/server-docs/event-subscription-guide/overview)
- [配置请求网址与加密](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/request-url-configuration-case.md)
- [Encrypt Key 加密配置案例](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/encrypt-key-encryption-configuration-case?lang=zh-CN)
- [接收消息事件 `im.message.receive_v1`](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive)
- [获取会话历史消息](https://open.feishu.cn/document/server-docs/im-v1/message/list?lang=zh-CN)
- [话题概述](https://open.feishu.cn/document/im-v1/message/thread-introduction)
- [回复消息](https://open.feishu.cn/document/server-docs/im-v1/message/reply)
