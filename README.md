# 🥚 鸡蛋收割机（Egg Harvester）

> 一个运行在自己电脑上的群消息与 API Key 整理工具。它把已授权的 QQ、微信和公开网页信息集中到一个本地工作台，帮你找出 API 配置、检查是否能用，并按来源和状态整理保存。

## 一句话说明

**把散落在群聊和网页里的信息收进来 → 自动识别 API 地址和 Key → 脱敏展示并测试连通性 → 只在本机生成可搜索的情报与简报。**

这是一个本地优先项目：默认 Web 服务只监听 `127.0.0.1:8890`，不会把聊天内容或 Key 自动上传到第三方。请只处理你有权访问和使用的数据。

## 适合做什么

- 从 QQ / NapCat、微信本地数据库和公开网页收集消息。
- 从普通消息、截图和多组日报中识别 `Base URL`、普通 Key 或 Base64 编码的 Key。
- 把同一组的地址和 Key 正确配对，自动去重。
- 测试 API 是否连通，并显示模型、延迟、错误原因和脱敏 curl 日志。
- 标出“群里说可能失效”和“实时测试失败”这两种不同状态。
- 在本地管理供应商、鸡蛋站、群文件、消息上下文、Doge 素材和 AI 简报。

## 当前不做什么

- 不替用户登录 Telegram、Discord 或飞书。
- 不要求把 Telegram / Discord / 飞书 Token、App Secret 或个人凭据交给助手。
- 不把密钥写入 GitHub；本地数据库可能保存已验证 Key 的明文，这是为了重新测试和导入配置，必须保护好本机文件。
- 不把公开网页采集误当成用户授权的私聊数据；QQ、微信等来源必须由操作者自行配置并确认授权。

## 快速开始

```bash
git clone https://github.com/langcher752-droid/egg-harvester-doge-den.git egg-harvester
cd egg-harvester
cp .env.example .env
make install
make test
make run
```

打开 <http://127.0.0.1:8890/>。

## 常用入口

| 入口 | 用途 |
|---|---|
| `/` | 消息流、群聊、手工收割和供应商管理 |
| `/intel` | 情报简报 |
| `/files` | 群文件与安全检查 |
| `/doge` | Doge 动画和素材 |
| `/stations` | 鸡蛋站 / API 中转站目录 |

## 支持的来源

| 来源 | 方式 | 说明 |
|---|---|---|
| QQ / NapCat | OneBot HTTP | 需要操作者自行部署并配置 `NAP_HTTP_URL` |
| 微信 | 本地数据库轮询 | 仅 macOS；需要用户自己的 `WECHAT_WXID`、`WECHAT_CODE` 和数据库路径 |
| AI 福利网页 | 定时公开网页采集 | 不登录、不互动 |
| 买鸡鸡go、白嫖社区、羊毛站目录 | 公开 API / 网页 | 只读采集，提取到的 Key 仍会经过连通性验证 |

## 手工收割支持的格式

可以粘贴普通配置：

```text
https://api.example.com/v1|sk-your-key
```

也可以粘贴群日报：

```text
【第1组】
Base URL
https://api.example.com/v1
Jia mi Base 64
<完整的 Base64 字符串>
可用模型
deepseek-v4
```

界面中的“预览解析/解码”只在本地解析，不会联网；点击“开始收割”后才会进行连通性测试。Key 在界面和 curl 日志中都会脱敏。

## 安装与测试

本地 Python：

```bash
make install
make test
make lint
```

Docker 只运行 Python/FastAPI 后端：

```bash
docker build -t egg-harvester:local .
docker run --rm -p 8890:8890 --env-file .env \
  -v "$(pwd)/runtime:/data" egg-harvester:local
```

macOS 原生客户端：

```bash
./app/build.sh --install
open "$HOME/Applications/鸡蛋收割机.app"
```

## 隐私和发布前检查

以下文件不得提交到 GitHub：`.env`、`eggs.db`、`media/`、聊天导出、二维码、设备文件、日志、个人配置和任何 API Key。发布前至少运行：

```bash
make test
git diff --check
git status --short
```

完整部署步骤见 [`DEPLOYMENT.md`](DEPLOYMENT.md)，运维交接见 [`交接文档-完整版.md`](交接文档-完整版.md)。

## License

当前仓库的许可证状态请以 [`LICENSE`](LICENSE) 为准；发布前请确认 Doge 素材、图标、第三方前端库和数据源具有可再分发许可。
