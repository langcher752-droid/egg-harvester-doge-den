# 🥚 Egg Harvester · 鸡蛋收割机

> **EN / 中文** — A local-first intelligence workbench for collecting, inspecting, and organizing messages, API eggs, and Doge memes.
> 面向本地优先的群情报工作台：采集、检查和整理消息、API 鸡蛋与 Doge meme。

---

## English

Egg Harvester is a local-first intelligence workbench. It collects messages from authorized QQ, WeChat, and web sources, extracts OpenAI-compatible API configurations, checks connectivity, and provides a local Web UI plus an optional macOS Swift shell.

### Highlights

- Message ingestion and intelligence summaries
- API-key extraction and connectivity checks
- Safe DSH Desktop profile import with backups and rollback
- Manageable **iconmeme** library: upload, name, select, and delete Doge images
- Doge mascot, state-face customization, themes, and operation feedback
- Offline unit tests, pinned dependencies, Docker support, and a reproducible Makefile workflow

### Quick start

```bash
git clone <your-repository-url> egg-harvester
cd egg-harvester
cp .env.example .env
make install
make test
make run
```

Open <http://127.0.0.1:8890/>.

### Docker

```bash
docker build -t egg-harvester:local .
docker run --rm -p 8890:8890 \
  --env-file .env \
  -v "$(pwd)/runtime:/data" \
  egg-harvester:local
```

Docker runs the Python/FastAPI backend. macOS WeChat integration, NapCat, and the Swift desktop shell remain optional host integrations.

### Privacy

This project can process private messages, images, and API credentials. Verified API keys are stored locally in SQLite in plaintext so they can be retested and exported; never commit `eggs.db`, `.env`, `media/`, logs, chat exports, QR codes, device files, or runtime data.

### License

MIT. See [`LICENSE`](LICENSE).

---

本项目是一个本地优先的群情报工作台：从已授权的 QQ、微信和网页来源收集消息，提取可用的 OpenAI-compatible API 配置，运行连通性检查，并提供本地 Web UI 和可选的 macOS Swift 外壳。

> **隐私警告**：本项目处理私人通信、图片和 API 凭据。默认 SQLite 数据库会保存已验证的 API key 明文，以支持重新测速和导入 DSH。不要把 `eggs.db`、`.env`、`media/`、聊天导出、二维码、设备文件或任何运行数据上传到 GitHub。

## 支持范围

| 组件 | 支持平台 | 说明 |
|---|---|---|
| FastAPI + Web UI | Linux / macOS | Docker 和本地 Python 均可运行 |
| iconmeme / Doge 素材库 | Linux / macOS | 文件存储在 `static/iconmeme/`，默认被忽略 |
| 微信本地库解密 | macOS，本机配置 | 需要用户自行提供 `WECHAT_WXID` 和 `WECHAT_CODE` |
| QQ / NapCat | 由用户部署 | 通过 `NAP_HTTP_URL` 访问 OneBot HTTP API |
| Swift 桌面外壳 | macOS | 需要 Xcode Command Line Tools；不在 Linux Docker 中运行 |

## 清洁安装

需要 Python 3.12.8、Node.js（仅用于前端语法检查）以及 Git。推荐使用项目内虚拟环境：

```bash
git clone <your-repository-url> egg-harvester
cd egg-harvester
cp .env.example .env
make install
```

填写 `.env` 中实际需要的网关或本地服务配置。所有敏感配置都只放在 `.env`，不要写入源码。

## 运行

```bash
make run
```

然后访问：

- Web 工作台：<http://127.0.0.1:8890/>
- Doge / iconmeme：<http://127.0.0.1:8890/doge>
- 情报简报：<http://127.0.0.1:8890/intel>
- 群文件决策：<http://127.0.0.1:8890/files>

首次运行会在 `DB_PATH` 指定的位置创建 SQLite 表。`DB_PATH` 为空时使用项目目录下的 `eggs.db`；该文件已被 Git 忽略。

## 测试和检查

离线单元测试不需要网关、QQ、微信、Docker 或已有数据库：

```bash
make test
make lint
```

`test_e2e.py` 是本机集成测试，只在后端已经运行、并且你明确允许它访问本地数据时执行：

```bash
.venv/bin/python test_e2e.py
```

## Docker

Docker 只提供 Python/FastAPI 后端，不包含 macOS 微信、Swift 外壳或 NapCat：

```bash
docker build -t egg-harvester:local .
docker run --rm -p 8890:8890 \
  --env-file .env \
  -v "$(pwd)/runtime:/data" \
  egg-harvester:local
```

容器默认使用：

- 数据库：`/data/eggs.db`
- 媒体：`/data/media`
- 服务：`0.0.0.0:8890`

## 可选集成

### QQ / NapCat

```dotenv
NAP_HTTP_URL=http://127.0.0.1:3000
NAP_CONTAINER=napcat
DOCKER_BIN=
```

如果 Docker 不在 `PATH`，才设置 `DOCKER_BIN`。不要把容器配置中的 token 提交到仓库。

### 微信

只有在已获得授权、并且明确需要本地微信库轮询时才填写：

```dotenv
WECHAT_WXID=your_wxid
WECHAT_CODE=your_code
WECHAT_DB_DIR=/path/to/xwechat_files/.../db_storage
WECHAT_ATTACH_DIR=/path/to/xwechat_files/.../msg/attach
```

未填写时微信解密功能保持关闭，不会使用仓库内的个人默认值。

### DSH Desktop

鸡蛋库中的导入功能写入当前用户的 DSH Desktop profile：

- `~/.dsh/profiles/desktop/cordis.patch.yml`
- `~/.dsh/.credentials.yaml`

导入使用 credential ref，不会把明文 key 写入 patch 文件。导入前请备份 DSH 配置，并确认你有权使用这些凭据。

## iconmeme 素材库

在 Web UI 的“设置 → Doge 个性化”中可以：

- 上传 PNG / JPEG / GIF / WebP；
- 设置显示名称；
- 设为吉祥物；
- 用作状态脸；
- 删除素材。

用户上传文件放在 `static/iconmeme/`，默认不会进入 Git。数据库只保存元数据和安全生成的文件名。

## macOS Swift 外壳

仅在 macOS 上执行：

```bash
./app/build.sh --install
"$HOME/Applications/鸡蛋收割机.app/Contents/MacOS/GroupIntel" --check
```

Electron 目录是历史客户端，不是当前维护的桌面入口；后端和原生 Swift 外壳是推荐路径。

## 发布前检查

```bash
make test
```

发布前必须确认：

1. `.env`、数据库、媒体和运行日志未被跟踪；
2. 没有个人微信 ID、微信解密码、API key、二维码或设备身份文件；
3. 已在全新 Linux checkout 中执行 `make install && make test`；
4. 已阅读并确认第三方依赖和数据源的使用许可；
5. GitHub 仓库设置为合适的私有或公开可见性。

## License

本仓库尚未声明统一开源许可证。发布到 GitHub 前，请补充 `LICENSE`，并确认 Doge 素材、图标、第三方前端库和数据源均具有可再分发许可。
