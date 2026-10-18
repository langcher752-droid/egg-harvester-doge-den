# QQ 群聊 KEY 汇总消息解析方案

> 状态：实现中；已完成长格式分组/Base64 key 解析和基础 probe endpoint 修正，尚未完成模型提示、valid/invalid/expired 状态落库及 UI 解码预览。用户示例为 2026-10-18 多群日汇总，包含 Base URL、`Jia mi Base 64` 密钥、模型、有效期、余额、获取时间、贡献者、备注和“暂未找到新增密钥”等段落。

## 目标

QQ 消息不只支持“单条消息中直接出现 API Key”。还要识别机器人或群成员转发的多群汇总/日报型长消息，按每个【第N组】分别提取 endpoint、Base64 编码 key、模型及上下文，并把观察到的失效信息用于风险标注。

## 解析流程

1. **消息级预处理**：保留原文；对全角冒号、CRLF、常见中英文字段名做归一化，不删除原始换行和组标题。
2. **按组切分**：识别 `【第\s*\d+组】`、`第N组` 等标题；若无组标题，将整条消息当单组。群名/群号/统计时间作为报告级上下文，组内字段继承群上下文。
3. **字段提取**：
   - Base URL：识别 `Base URL`、`接口地址`、`API地址`，解析并规范化 URL。
   - Encoded key：识别 `Jia mi Base 64`、`加密 Base64`、`Base64` 等提示行，解析下一非空行；也识别 `Bearer`/常见 key 前缀的原文 token。
   - Models：`可用模型` 后续行解析逗号、中文顿号、括号中的版本说明。
   - metadata：截止日期、余额、获取时间、贡献者、备注。
   - 空结果：识别“暂未找到新增密钥”“无新增密钥”等，记录报告状态，不创建 key。
4. **Base64 解码**：标准 Base64 严格校验并 UTF-8 解码；失败时保留候选原文和 `decode_status=invalid`，不得把乱码当 key。拒绝空值、过长数据和非预期控制字符；支持 URL-safe Base64 前需单独标记编码类型。
5. **验证**：只在用户配置的 provider probe 允许的情况下发出真实网络请求。不得把 Base64 解码等同“key 有效”。区分 `decoded/unverified`、`probe_valid`、`probe_invalid`、`endpoint_unreachable`、`reported_expired`。
6. **风险信息**：备注里“有人反馈用不了”“发布者说 G 了”“大概率已失效”属于来源说法，不是 probe 结果；保存原文并转成 `reported_status=possibly_expired`，仍需独立验证。
7. **去重**：以标准化 `base_url + decoded_key_hash` 去重，不把 key 本身放在日志。相同 key 再次出现时追加 source message、group、时间、模型和评论，而非重复创建。
8. **审计**：保留源消息 ID、平台/群、采集时间和 parser version，支持用户回看原文；默认只将明文 key 写入当前本地 key vault/database 的受控字段，UI 一律掩码。

## 智能手工输入 / Provider Endpoint

用户反馈手工输入不够智能：当前输入应支持从聊天摘要、配置片段或普通文本自动识别 `Base URL`、编码 key、模型和备注，而不强制用户手工选择固定的官方 OpenAI endpoint。

- 解析后展示“识别预览”，用户可逐项修改 Base URL、Key、模型、API 风格和备注。
- Base URL 由用户/消息提供；endpoint 绝不默认改成 `https://api.openai.com/v1/chat/completions`。
- 规范化规则只补齐必要路径，保留用户提供的网关前缀（如 `/v1`、`/api/paas/v4`）。连接测试优先按配置 Base URL 调用 `/models`；若用户指定 chat endpoint 或模型路由，应在兼容策略中小心 fallback，而不是覆盖成官方 URL。
- 测速日志中的 curl 需使用真实 endpoint、正确 JSON 和已脱敏密钥；禁止输出字面占位符 `{_mask(key)}`。
- 如果 endpoint 无法推断，显示“待填写/待验证”，不要悄悄使用官方 OpenAI 地址。
- 保存前展示明确的解析/解码/探测状态，允许只保存为未验证条目；不要把“无法连接”直接判成 key 无效。

## 样例行为期望

给定用户提供的长消息：

- 每个【第1组】形成独立候选；有 Base URL、Base64 密文、模型时，解码出 key 候选并关联对应 endpoint。
- `备注：... 00:31 ... 用不了 ... 11:43 ... G了` 只形成“来源报告疑似失效”标签；若网络 probe 后失败，再单独记录 probe 错误和时间。
- “暂未找到新增密钥”群组只生成空报告记录，不生成虚假候选。
- 用户原文出现打码 Key（例如 `sk-v6W****Sev6`）应标记 `redacted` 并禁止 probe。

## 测试清单

- CRLF/中文字段/多群多组/无组标题/重复发布。
- 标准 Base64、无效 Base64、URL-safe Base64、空白折行、被截断、加密密文被打码。
- OpenAI-compatible 自定义 endpoint、路径包含版本号、endpoint 自带 `/models`、无 endpoint。
- 报告为疑似失效但探测可用；报告称可用但探测不可达；429/代理故障不得标记 key invalid。
- 原文不泄露到日志，UI 掩码，来源链路可追溯。
