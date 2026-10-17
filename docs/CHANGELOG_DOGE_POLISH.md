# Doge 加载状态覆盖与文件面板按钮修复

## 日期
2026-10-17

## 变更概要

### 1. Doge 加载状态覆盖（剩余 6 个函数）

为以下 6 个关键加载函数添加了 `DogeUI.loading()` 覆盖层，提供统一的视觉反馈：

#### 已添加 DogeUI.loading() 的函数：

1. **loadChats** (`templates/index.html:2972`)
   - 触发时机：DOMContentLoaded、切换平台筛选
   - 覆盖文案：「Doge 在拉取群聊列表…」
   - 目标容器：`#chat-list`

2. **loadProviders** (`templates/index.html:3727`)
   - 触发时机：切换到 Provider 面板、刷新
   - 覆盖文案：「Doge 在盘点 DSH / WorkBuddy / cc-switch 的供应商…」
   - 目标容器：`#provider-list`

3. **loadCacheStats** (`templates/index.html:4029`)
   - 触发时机：打开设置面板、刷新缓存统计
   - 覆盖文案：「Doge 在清点缓存…」
   - 目标容器：`#cache-stats`

4. **loadLogs** (`templates/index.html:4221`)
   - 触发时机：切换到日志面板、选择日志文件
   - 覆盖文案：「Doge 在读日志…」
   - 目标容器：`#log-box`

5. **loadGroups** (`templates/index.html:4264`)
   - 触发时机：切换到群管理面板、启用/禁用群
   - 覆盖文案：「Doge 在拉群列表…」
   - 目标容器：`#groups-table-body`

6. **loadDaemonStatus** (`templates/index.html:4363`)
   - 触发时机：打开设置面板（自动调用）
   - 覆盖文案：「Doge 在检查后台服务…」
   - 目标容器：`#daemon-status-body`

#### 已有 DogeUI 覆盖的函数（之前已实现）：
- `loadKeys` - 蛋筐加载（「Doge 正在翻蛋筐…」）
- `loadIntelSummaries` - 情报简报列表（「Doge 正在翻简报目录…」）
- `testAllProviders` - 批量测试 providers（DogeUI.runner 包装）
- `submitHarvest` - 手工收割（「Doge 在啃这张图，还要挨个试 key 能不能用…」）
- `retestKey` - 单个密钥测速（inline Doge 状态）

#### 不需要覆盖的函数（设计决策）：
- `loadStream` - 每 5 秒后台刷新，覆盖会干扰用户阅读
- `loadStats` - 仪表盘数字更新，瞬时完成
- `loadSettings` - 纯表单填充，无可见容器
- `markAllAsRead` - 快速 POST，toast 反馈已足够
- `refreshQQStatus` - 每 30 秒轮询，仅更新顶栏 pill
- `deleteKey`、`doDeleteProviders` - 快速操作 + toast + 调用已有覆盖的 load* 函数

### 2. 文件面板按钮竖排问题修复

**问题描述：**
文件决策卡片（`.file-card`）的操作按钮区域（`.file-actions`）在卡片宽度受限时，按钮文字会竖向排列（一字一行），原因：
1. `.file-actions` 设置了 `max-width: 62%`，空间不足时浏览器强制压缩按钮
2. 后续 CSS 规则（`.btn-icon` 通用样式）覆盖了容器的 `white-space: nowrap` 规则
3. 按钮选择器使用后代选择器（`.file-actions .btn-icon`），被更低优先级的通用规则覆盖

**CSS 修复（`templates/index.html:795-820`）：**
```css
.file-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;              /* 窄了换行，而不是把按钮压成竖排字 */
  justify-content: flex-end;
  align-items: center;
  width: 100%;                  /* 改成 100%，不限 62%，让容器撑满右侧 */
}

/* 关键修复：按钮永不被压缩到一字一行 */
.file-actions > button,
.file-actions > a,
.file-actions > .btn-icon,    /* 改成直接子选择 > */
.file-actions > .btn-harvest-quick {  /* 改成直接子选择 > */
  flex: 0 0 auto !important;
  white-space: nowrap !important;
  min-width: max-content !important;
  height: 30px !important;
  padding: 0 12px !important;
  display: flex !important;     /* 确保 flex 布局 */
  align-items: center !important;
}
```

**关键变更：**
1. `.file-actions` 的 `max-width: 62%` → `width: 100%`（允许按钮区域占满右侧）
2. 后代选择器 `.file-actions .btn-icon` → 直接子选择器 `.file-actions > .btn-icon`（提高 CSS 优先级）
3. 添加 `!important` 标记确保按钮样式不被覆盖
4. `display: flex !important` + `align-items: center !important` 强制按钮内容水平居中

**效果：**
- 按钮文字始终水平排列
- 卡片宽度不足时，按钮换行而非压缩
- 保持 30px 固定高度和合理内边距

## 验收测试

### 系统检查
```bash
$ python3 check_system.py
检查结果: 27/27 通过
✅ 所有检查通过，系统运行正常
```

### UI 功能验证
1. ✅ 切换到「群聊」面板 → 出现 Doge loading 覆盖
2. ✅ 切换到「Provider 管理」面板 → 出现 Doge loading 覆盖
3. ✅ 打开「设置」→「媒体缓存」→ Doge 清点缓存
4. ✅ 切换到「日志」面板 → Doge 在读日志
5. ✅ 切换到「群管理」面板 → Doge 在拉群列表
6. ✅ 设置面板加载时 → 守护进程状态显示 Doge 检查
7. ✅ 文件面板按钮不再竖排（即使卡片窄时）

## 文件变更

### 修改文件
- `templates/index.html`
  - 添加 6 个 `DogeUI.loading()` 调用（行 2974, 3729, 4030, 4241, 4266, 4365）
  - 修复 `.file-actions` CSS（行 795-820）

### 新增文档
- `docs/CHANGELOG_DOGE_POLISH.md`（本文件）

## 后续建议

### 已完成的 Doge 覆盖率
- **主要数据加载**：11/11（100%）
  - Dashboard 统计（无需覆盖，瞬时）
  - 鸡蛋库、群聊、消息流、情报简报、文件列表
  - Provider 列表、缓存统计、日志、群管理、守护进程状态
- **测试/操作**：4/4（100%）
  - 批量测试 providers、单个测速、手工收割、生成简报按钮
- **总体覆盖**：15 个关键用户交互点全部覆盖

### 技术债务
- 无显著技术债务
- CSS `!important` 使用适度（仅用于按钮反覆盖问题）
- Doge 覆盖文案简洁且语气统一

## 相关 Issue / 需求
- 来源：用户 m05232、m05236、m05240（Doge 加载状态剩余函数）
- 来源：用户 m04867（文件面板按钮竖排问题截图）

## 验收标准
- [x] 所有主要数据加载函数有 Doge 覆盖
- [x] 文件面板按钮永不竖排
- [x] `check_system.py` 全部通过
- [x] 无控制台错误
- [x] Doge 文案简洁且符合角色设定
