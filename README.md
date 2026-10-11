# OCS-JS AI Answer Service

为 [OCS-JS](https://github.com/ocsjs/ocsjs) 提供兼容 AnswererWrapper 的本地 AI 题库接口，支持纯文字题及 **OCS 5.1.0 图片题优化**。服务基于 Flask + OpenAI Responses API。

支持两种独立鉴权方式：

- **`AUTH_MODE=siwc`（推荐）**：通过 OpenAI 官方 **Sign in with ChatGPT** 授权开源应用，在账户允许的范围内使用 ChatGPT 计划额度，无需单独充值 API。受账号资格、模型可用性与订阅额度限制。
- **`AUTH_MODE=api_key`**：使用单独计费的 OpenAI API Key 或兼容供应商密钥。

**不支持直接读取 Codex CLI 登录文件并当成普通 API Key 使用。** 两种模式互不混用，也不需要安装 ChatGPT Plugin/MCP。

## ⚠️ 重要提示

> [!IMPORTANT]
> - 本项目仅供个人学习使用，不保证稳定性，也不提供技术支持。
> - 请在遵守 OpenAI 使用条款和适用法律法规的前提下使用，不得用于非法用途。
> - 根据《生成式人工智能服务管理暂行办法》等规定，不得向中国地区公众提供未经必要备案的生成式人工智能服务。
> - 使用者应当遵守相关法律法规并承担相应责任；AI 生成内容不保证准确。

## 快速开始（Linux / Fish / Bash）

```bash
git clone https://github.com/LynnGuo666/ocsjs-ai-answer-service.git
cd ocsjs-ai-answer-service
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

用 `.venv/bin/python` 运行不依赖 Shell 类型；如果要手动激活虚拟环境，Fish 使用 `source .venv/bin/activate.fish`，Bash/Zsh 使用 `source .venv/bin/activate`。

### A. 通过 ChatGPT 订阅共享额度

1. 使用 `.venv/bin/python siwc_login.py` 打开 OpenAI 官方授权页面，自行确认应用权限。凭据保存在 `~/.config/ocsjs-ai-answer-service/siwc.json`，不会使用 Codex 登录凭据。
2. 执行 `.venv/bin/python siwc_models.py` 查询**当前账户实际可用**的模型。
3. 编辑 `.env`，设置 `AUTH_MODE=siwc`、`OPENAI_MODEL=<可用模型>`，以及随机 `ACCESS_TOKEN`（使用 `openssl rand -hex 24` 生成）。`ACCESS_TOKEN` 是**本机题库服务的密钥**，不是 OpenAI OAuth Token。
4. 运行 `.venv/bin/python app.py`，访问 `http://127.0.0.1:5000/api/health`，应显示 `auth_mode=siwc_plan`。

首次运行还没有本地题库配置时，复制 `ocs_config_siwc_example.json` 为 `ocs_config_siwc.local.json`，填入对应的 `X-Access-Token`。本地 `*.local.json` 会被 Git 忽略。

### B. 使用普通 API Key

在 `.env` 中设置 `AUTH_MODE=api_key` 和 `OPENAI_API_KEY=...`，选择供应商支持的模型；调用产生的费用由 API 供应商收取。访问控制仍建议使用单独的 `ACCESS_TOKEN`。

## OCS 5.1.0 题库配置（支持图片题）

在 OCS 中粘贴 `ocs_config_siwc.local.json` 的完整 JSON；公开示例见 `ocs_config_siwc_example.json`（其中不包含有效密钥）。这是 **POST + JSON** 配置：

- 原字段 `title`、`options`、`type` 保留。
- `images` 使用 `{"handler":"return (env) => env.images || []"}` 返回图片数组，而**不是**字符串 `"${images}"`。
- `suggestion_title` / `suggestion_options` 带 `[图片1]` 等占位符，用于让模型识别图片所对应的题干或选项。
- OCS 的「通用 → 全局设置 → 高级设置 → 图片题优化」应开启。
- 旧版 `GET /api/search` 仍兼容纯文本题，但图片题请使用 POST 模板。

**连接检测 403 已修复**：OCS 5.1.0 的题库连接测试会发送**不带自定义请求头的 HEAD**。服务对 `HEAD /api/search` 返回 204；真正答题的 GET/POST 仍要求正确的 `X-Access-Token`。连接测试通过不代表模型答案一定准确，请用实际题目复核。

## 文档

- [SIWC_SETUP.md](SIWC_SETUP.md)：ChatGPT 授权、模型查询、OCS 图片题配置和 401/403 排查
- [api_docs.md](api_docs.md)：`/api/search` 请求/响应、限额和状态码
- [ocs_config_siwc_example.json](ocs_config_siwc_example.json)：可公开发布的题库配置示例

## 本地管理面板与测试

打开 `http://127.0.0.1:5000/dashboard` 会要求 HTTP Basic 登录：**用户名 `ocs`，密码是本地 `ACCESS_TOKEN`**。答题记录仅在内存里保留最近 100 条；网页响应禁止缓存，第三方站点无法通过宽松 CORS 读取它。浏览器 HTTP Basic 仅用于查看管理面板，不授权实际答题请求。

只检查连接与鉴权（不消耗模型请求）：

```bash
.venv/bin/python test_service.py
```

需要实际测试模型时，显式加 `--inference`。运行离线单测：`.venv/bin/python -m unittest test_siwc.py test_multimodal.py`。

## 安全和部署

`.env`、`*.local.json`、`siwc.json`、本地凭据、日志、缓存和虚拟环境不会被提交到 Git；`.dockerignore` 还防止私密文件随 Docker `COPY . .` 进入镜像。**请勿提交或分享你的本地 JSON / OAuth 凭据**。

不要将 `ACCESS_TOKEN` 放入 URL 查询参数；真实答题仅支持请求头（或本地面板的 HTTP Basic）。建议始终以 `HOST=127.0.0.1` 在本机运行，不要将订阅授权接口作为公开代理、收费转发服务。SIWC 的订阅共享额度有资格和使用限制，不能视作无限免费 API。

如使用容器，Compose 已配置容器内部监听 `0.0.0.0`、宿主机仅监听 `127.0.0.1`，且通过目录挂载（而不是 Docker COPY）访问 OAuth 凭据。容器默认以 UID/GID 1000 运行；宿主机不是这个用户 ID 时，请设置 `OCS_UID` / `OCS_GID` 与本机 `id -u` / `id -g` 一致，否则无法读取/刷新 0600 凭据。

本项目基于 [LynnGuo666/ocsjs-ai-answer-service](https://github.com/LynnGuo666/ocsjs-ai-answer-service) 改造。仅供合规的个人学习、开发及已获允许的环境使用，模型回答可能有误，请自行校验。
