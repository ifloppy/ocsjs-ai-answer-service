# 用 ChatGPT Plus/Pro 订阅额度为 OCS 提供 AI 题库

本项目支持 OpenAI 2026 年 9 月发布的 **Sign in with ChatGPT（SIWC）** 官方开源应用授权流程。
它不是将 Codex CLI 的 OAuth Token 当成普通 API Key：登录界面会明确向用户请求
`chatgpt.tokens.use.direct` 权限。符合资格时，可在订阅包含的使用量内运行 Responses API。

## 快速操作

```bash
cd ocsjs-ai-answer-service
# Bash/Zsh: source .venv/bin/activate
# Fish:     source .venv/bin/activate.fish
# 以下命令适用于所有 Shell，不需要激活虚拟环境：
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python siwc_login.py
```

此时默认浏览器会打开 **auth.openai.com** 官方登录页面，手动同意
"OCS-JS AI Answer Service" 的权限。该步骤必须由本人完成。
成功后会写入仅当前用户可读的：
`~/.config/ocsjs-ai-answer-service/siwc.json`。
凭据不会上传到源码仓库，也不会复用 ~/.codex/auth.json。

然后编辑项目 `.env`，**新增**：

```dotenv
AUTH_MODE=siwc
# 下方命令生成随机密钥并填写到 ACCESS_TOKEN：openssl rand -hex 24
ACCESS_TOKEN=REPLACE_WITH_YOUR_RANDOM_LOCAL_ACCESS_TOKEN
OPENAI_API_BASE=https://api.openai.com/v1
OPENAI_API_MODE=responses
HOST=127.0.0.1
DEBUG=False
```

服务只支持 `AUTH_MODE=siwc` 和 `AUTH_MODE=api_key`。请勿把其他工具的登录凭据当作 API Key 使用。
官方 SIWC 需要 `store=false`、`stream=true`、`input` 数组；服务会自动强制使用。
不传递 `max_output_tokens` 与 `service_tier`。

```bash
.venv/bin/python app.py
# 另一终端
curl -s http://127.0.0.1:5000/api/health
```

在本机 OCS-JS 中优先使用 `ocs_config_siwc.local.json` 作为题库配置（含本地密钥，已经被 Git 忽略）；也可复制公开模板 `ocs_config_siwc_example.json`，自行填写 `X-Access-Token`。脚本使用 `X-Access-Token` 请求头传递密钥，避免在 URL 和日志中暴露它。SIWC 模式强制设置此本地密钥，防止其他网页消耗订阅额度。无需构建 MCP。

## 2026 年 OCS-JS 图片题优化适配

OCS 更新后的题库配置需要使用 **POST JSON**，携带原始 `title`、`options`、`type`，以及新增的 `images`、`suggestion_title`、`suggestion_options`。图片题优化会生成 `data:image/png;base64,...` 数组和 `[图片1]` 占位符；纯文本题会发送空数组，仍正常处理。

- **此电脑直接使用** `ocs_config_siwc.local.json` 的完整 JSON（文件包含你已有的本地访问密钥，并被 Git 忽略；不要发给别人）。
- **公开配置模板** `ocs_config_siwc_example.json` 需要先填 `X-Access-Token`。
- 在 OCS 的「通用 → 全局设置 → 高级设置」打开「图片题优化」。该选项对新 POST 模板有效。
- OCS 中的 `images` 字段一定要是 `{ "handler": "return (env) => env.images || []" }`，而不是字符串 `"${images}"`。`suggestion_title` 和 `suggestion_options` 也通过字段解析器带入，纯文本时回退到原字段。
- Flask 只接受 Base64 Data URL，不主动请求外部图片链接；对图片设置了数量和体积上限，并对图片内容计算缓存指纹。多模态调用只支持 Responses API。
- **旧的 GET 模板仍能用于纯文本题，但不能承载此新图片数组。**

参照 OCS 最新发布用户脚本源码：https://greasyfork.org/scripts/457151-ocs-%E7%BD%91%E8%AF%BE%E5%8A%A9%E6%89%8B/code

## OCS 5.1.0 配置页显示 403 的修复

OCS 5.1.0 的 `probeAnswerer()` 连接检查不使用题库配置里的 POST 和请求头，
而是直接发送 **HEAD /api/search?t=...**。因此带 `ACCESS_TOKEN` 的真实答题 POST 正常，
连接检查却会被旧版服务误拒绝为 403。

本项目已将 `HEAD /api/search` 处理为无需 Token 的 **204 No Content**；
这个探测不会调用模型，也不返回答案或令牌。**GET/POST 答题仍要求正确的
`X-Access-Token`**，不必把令牌放到 URL 里，也不必关闭鉴权。

升级代码后重启 Flask，再在 OCS 里刷新配置弹窗重试。可用：
`curl -I 'http://127.0.0.1:5000/api/search?t=123'`
验证，应显示 `HTTP 204`。

## 模型列表与 401 排查

在获取 SIWC 独立授权后，先查询当前账户能用的模型，而不是盲猜 Codex 中的模型。
OAuth 授权下的模型 catalog 在 `GET https://api.openai.com/v1/models`。
如当前配置的 `OPENAI_MODEL` 不在返回结果中，改为目录中的 `slug`。
账号没有资格、授权时拒绝权限、用途不受支持等情况也可能得到 401/403，
切勿通过伪造或替换请求头绕过权限。

获取一次新的 SIWC 授权与刷新令牌由登录工具和 `siwc_auth.py` 完成。
服务端每次调用前检查到期时间，临近过期时将自动刷新，并序列化多进程刷新，避免刷新令牌旋转冲突。
如果 OAuth 令牌被撤销，重新运行 `python siwc_login.py`。

用户可前往 [ChatGPT 用量设置](https://chatgpt.com/#settings/Usage) 查看计划与应用限制。
Plus 计划在多个已授权应用之间共享使用额度；它并不是无限制的独立 API。
只用于本机、个人授权的应用，不得将这些凭据作为公网通用 API 出售/转发。

## ChatGPT Plugin / MCP 为什么不是反向模型接口

MCP 的标准方向是 ChatGPT 调用**工具服务**，并不是工具服务可以随意调用
ChatGPT 对话模型。给 ChatGPT 安装一个题库 MCP，只能让 ChatGPT 在交互时使用它，
无法给 OCS-JS 提供稳定的请求-响应 API。
本项目采用：OCS -> 本地 Flask -> 官方 SIWC Responses API -> OCS。

## 官方文档

- https://developers.openai.com/siwc/quickstart
- https://developers.openai.com/siwc/token-sharing-open-source/sign-in
- https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference
- https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations
- https://openai.com/policies/sign-in-with-chatgpt-terms/

## 本地管理面板

`/dashboard` 会使用 HTTP Basic 验证本地访问密钥（用户名 `ocs`、密码与 `.env` 里的 `ACCESS_TOKEN` 一致）。不用把密钥放进 URL。默认关闭任意跨域访问；OCS 推荐的 Tampermonkey `GM_xmlhttpRequest` 不依赖该 CORS 设置。

## 提交源码前的文件安全检查

`git status --ignored --short` 应显示 `.env`、`.venv/`、`ocs_config_siwc.local.json` 被忽略；`git ls-files` 不应包含这些私密文件。`.dockerignore` 另外防止 `Dockerfile` 中的 `COPY . .` 把密钥加入镜像。

`.env` 中只需 `AUTH_MODE=siwc`、`ACCESS_TOKEN` 等项目配置；不再读取 Codex CLI 的 `auth.json`。OAuth 凭据位于用户配置目录，不能上传或镜像打包。
