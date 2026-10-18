#!/usr/bin/env bash
# 飞书话题群配置人工向导：只引导控制台步骤，不收集/存储 App Secret 等凭证。
set -euo pipefail

if [[ -t 1 ]] && command -v tput >/dev/null 2>&1 && [[ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]]; then
  BOLD=$(tput bold); DIM=$(tput dim); RESET=$(tput sgr0); BLUE=$(tput setaf 4); GREEN=$(tput setaf 2); YELLOW=$(tput setaf 3)
else
  BOLD=""; DIM=""; RESET=""; BLUE=""; GREEN=""; YELLOW=""
fi

TOTAL_STAGES=7
CURRENT=0
pause() { printf '  %s ' "$1"; read -r _ || true; }
stage() { CURRENT=$((CURRENT + 1)); printf '\n%s%s▸ 步骤 %s/%s：%s%s\n' "$BOLD" "$BLUE" "$CURRENT" "$TOTAL_STAGES" "$1" "$RESET"; }
open_url() { printf '  %s↗%s %s\n' "$GREEN" "$RESET" "$1"; { command -v open >/dev/null 2>&1 && open "$1"; } >/dev/null 2>&1 || true; }
warn() { printf '  %s⚠ %s%s\n' "$YELLOW" "$1" "$RESET"; }

printf '\n%s飞书话题群事件订阅配置向导%s\n' "$BOLD" "$RESET"
printf '本向导不会要求你把 App Secret、Encrypt Key、Verification Token 或隧道凭证粘贴到终端、聊天或仓库。\n'
warn '飞书云端回调要求公网可达的 HTTPS URL；仅本机 127.0.0.1 不够。'
pause '准备好后按 Enter 开始。'

stage '创建企业自建应用'
open_url 'https://open.feishu.cn/app'
printf '  创建企业自建应用，填写名称和说明。\n'
printf '  在“凭证与基础信息”页自行保管 App ID 和 App Secret；不要把 Secret 发给助手。\n'
pause '应用创建完成后按 Enter。'

stage '添加能力并申请最小权限'
printf '  在“添加应用能力”添加机器人能力。\n'
printf '  在“权限管理”申请接收群消息事件、读取会话历史/消息详情权限；只有需要下载附件时才申请资源权限。\n'
printf '  如果不需要机器人回复，不申请发送消息权限。\n'
pause '权限配置并完成管理员审批/发布后按 Enter。'

stage '准备公网 HTTPS 回调'
printf '  部署回调服务并准备类似 https://你的域名/feishu/events 的 URL。\n'
printf '  可用自有服务器反代；开发测试可使用受信任隧道，但不要长期依赖临时隧道。\n'
printf '  回调 URL 必须能被飞书云端访问，并正确响应 URL challenge。\n'
pause '确认 HTTPS URL 已可公网访问后按 Enter。'

stage '配置事件订阅与加密'
printf '  在“事件与回调/事件订阅”启用订阅，配置 HTTPS Request URL。\n'
printf '  设置 Verification Token 与 Encrypt Key，保存在本机 Keychain/密钥管理器；不要放进 Git 或日志。\n'
printf '  添加 im.message.receive_v1 事件，保存并按控制台要求发布新版本。\n'
pause 'URL challenge 成功且事件已发布后按 Enter。'

stage '将应用加入测试话题群'
printf '  在飞书客户端打开目标群设置/应用管理，将已发布应用加入测试话题群。\n'
printf '  若无法添加，请租户管理员检查应用可见范围、审批和群策略。\n'
printf '  不要假设“不需要群主逐个同意”就代表所有租户/群都自动可见。\n'
pause '应用已加入测试群后按 Enter。'

stage '端到端验证 Topic 消息'
printf '  依次发送普通群消息、话题根消息、话题内回复、图片和文件。\n'
printf '  在本地回调日志核对 message_id/chat_id/thread_id/root_id/parent_id，脱敏保存样例。\n'
printf '  再单独测试历史消息 API；实时事件收到不代表历史可回填。\n'
pause '验证完成后按 Enter。'

stage '本地录入凭证并设置范围'
printf '  在鸡蛋收割机“信息来源 → 飞书应用”本地界面录入 App ID、Secret、Verification Token、Encrypt Key、HTTPS URL。\n'
printf '  凭证应直接输入应用并加密保存，不经由本向导或聊天传递。\n'
printf '  只勾选获授权的测试群/话题；确认事件幂等和 Topic 关系正确后再扩大范围。\n'
pause '按 Enter 结束。'

printf '\n%s✓ 向导完成：请保留飞书控制台的权限/发布记录，并在应用中确认回调、权限和话题测试状态。%s\n' "$GREEN" "$RESET"
