# 新增数据源：买鸡鸡go（Discourse）

## 日期
2026-10-17

## 背景
用户要求把 `https://www.mjjgo.com` 加入情报采集管线。该站是 Discourse 论坛
（"买鸡鸡go"），与 baipiao（Flarum）不同，走 Discourse 原生 JSON API。

## 实现

### 新文件 `mjjgo.py`（139 行）
- 列表：`GET https://www.mjjgo.com/latest.json?page=N`（每页 30 帖，取 1 页）
- 正文：`GET /t/<id>.json` → `post_stream.posts[].cooked`（HTML → 去标签纯文本）
- 提取：复用 `baipiao.KEY_RES`（sk- / sk-ant- / wbk_ / AIza / gsk_），`probe` 验证后
  `store_valid_key("web:mjjgo", ...)`，明文 key 仅在验证通过后落库
- 落盘：`store.save_raw("web", "mjjgo", f"mjj:{id}", chat_name="买鸡鸡go", ...)`
- 礼貌限速 0.6s/帖；不登录、不回帖、不点赞

### 接入
- `web_watch.py`：`import mjjgo` + `once()` 内 `mjjgo.ingest()`，日志格式
  `[web] welfare=… baipiao=… mjjgo={topics, posts_parsed, new_keys}`
- `check_system.py`：新增「买鸡鸡go采集」检查 → 27/27 变 **28/28**
- `templates/index.html` `postLink()`：支持 `mjj:<id>` → `https://www.mjjgo.com/t/<id>`
  的「查看原帖 ↗」；原先只支持 baipiao `bp:<id>`

## 验证
- `./venv/bin/python mjjgo.py`：30 帖全部抓取成功，30 条入库
- 服务重启后 `logs/web-watch.log`：
  `mjjgo={'topics': 30, 'posts_parsed': 30, 'new_keys': 0}`
- `check_system.py` 28/28、`test_e2e.py` 12/12
- App `--eval` 验证 `postLink({platform:"web",msg_key:"mjj:20"})` 输出正确链接

## 备注
首轮 0 个新 key（该站帖子以闲聊/测评/交易为主）。若后续需要更高产出，
可把采集范围扩到「情报」分类（category_id=4）或增大页数（`MAX_PAGES`）。
