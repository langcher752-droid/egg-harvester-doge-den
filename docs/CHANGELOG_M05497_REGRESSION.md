# m05497 回归修复（UI 卡死 + 滚动 + 通知）

## 日期
2026-10-17

## 用户投诉（m05497）

> 「UI 完全没了」+ 两个编号问题：
> 1. 「每次刷新消息都会到底下，往上翻的人很不舒服」
> 2. 「通知点击了解详细后消失的太快，然后主窗口只能看 1 条上下文，而不是可以一直翻」

## 根因与修复

### 1. 左侧会话列表卡死 /「UI 完全没了」

**根因**：上一轮给 `loadChats()`（`templates/index.html`）加了
`DogeUI.loading(el, 'Doge 在拉取群聊列表…')`，但它被自动刷新每 5 秒调用一次，
第一次 fetch 完成后 `renderChatList()` 的签名早退（`chatSig === lastChatSig` 时 `return`）
会让列表永久停在 loading spinner 上。

**修复**：去掉 `loadChats()` 里的 DogeUI.loading，改为条件空态
（仅当 `!chatData.length && !el.dataset.loadingShown` 时显示一次性「加载会话…」），
后续 5 秒刷新不再触碰列表 DOM。

### 2. 原始 HTML 泄漏模板语法 `${DogeUI...}`

**根因**：上一模型把 JS 模板字面量误写进静态 HTML：
- `#btn-gen-intel` 按钮文本含 `${DogeUI.face('read','sm')}`
- `#cache-stats` 占位 img `src="${DogeUI.path('loading')}"`

**修复**：按钮改纯文字「一键提炼所有群简报」；img src 硬编码
`/static/doge/stickers/49_ai_sleeping_pillow.webp`。其余 `${DogeUI...}` 均在 JS 模板字面量内，无需改。

### 3. 自动刷新强制滚回底部（投诉 #1）

**修复**（`applyStream` 全函数重写 `templates/index.html`）：
- `nearBottom` 重命名为 `pinBottom = forceBottomOnce || dist < 100`（阈值 90→100px）。
- 静默刷新且用户已上翻时：只在 **真正有新增消息**（`hasNew`）才弹 pill，且这一分支
  现在会同步 `lastStreamSig` 与 `window.__latestId`，避免 read 标记变更误闪「↑ 有新消息」。
- 需要重建 DOM 时先记录 `prevTop`，非贴底分支重建后用 `scroll.scrollTop = prevTop` 恢复原位，
  不再无条件 `scroll.scrollTop = scroll.scrollHeight`。

### 4. 事件上下文只能看 1 条（投诉 #2）

**修复**（`openEventContext` `templates/index.html`）：
- 原逻辑只用 `ev.related_messages` 的 `by_ids` 取消息（事件常只有 1 条 related）。
- 现在先用 by_ids 拿触发消息的 `platform/chat_id/ts`，再按该群拉
  `start_ts=min-300 & end_ts=max+300 & limit=300` 的宽窗口，消息升序渲染、
  高亮第一条 related 消息，让用户能一直往上翻。

### 5. 通知卡片消失太快（投诉 #2）

**修复**（`app/src/ToastCenter.swift`）：自动关闭时长 `8s → 12s`（`asyncAfter`、进度条动画 duration、注释三处同步）。

## 验证

- `check_system.py`：27/27 通过
- `test_e2e.py`：12/12 通过
- `node --check`（抽取内联 JS）：通过
- `--snapshot --goto stream --eval`：`applyStream`/`openEventContext` 为 function、
  `#btn-gen-intel` 纯文字、`#chat-list` 渲染 20 个会话项（不再卡 spinner）
- App 已重建并安装（2.7M，1.2.0 build 3）
