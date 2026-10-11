# AI 题库服务 API 文档

OCS 5.1.0 适配版：本地 Flask 服务支持 OpenAI 官方 Sign in with ChatGPT（`AUTH_MODE=siwc`）和单独 API Key（`AUTH_MODE=api_key`）。详见 [README.md](README.md) 与 [SIWC_SETUP.md](SIWC_SETUP.md)。

## 鉴权和服务状态

- `GET /api/health`：公开的无敏感信息健康检查，返回 `status`、`model`、`auth_mode` 等。
- `HEAD /api/search`：专门兼容 OCS 5.1.0 的无凭据连通性探测，返回 **204**，不消耗模型额度。
- `GET /api/search` 或 `POST /api/search`：实际答题。若设置 `ACCESS_TOKEN`，须通过 `X-Access-Token` 请求头认证。缺失/错误返回 **403**。
- `POST /api/cache/clear`、`GET /api/stats`：使用相同的本地访问令牌。
- `GET /dashboard`：显示最近的本地答题记录，需要 HTTP Basic（用户名 `ocs`，密码 `ACCESS_TOKEN`）；禁止浏览器缓存。

本服务不接受 URL 的 `?token=...`，也不开放任意站点的跨域读取。实际答题只能通过 `X-Access-Token` 请求头认证，HTTP Basic 仅用于只读管理面板。`ACCESS_TOKEN` 是本地题库接口密钥，不等于 SIWC OAuth Token，也不是 `OPENAI_API_KEY`。使用 ChatGPT 订阅共享时必须设置本地 `ACCESS_TOKEN`。

## POST /api/search（推荐）

Content-Type: `application/json`

| 参数 | 类型 | 说明 |
|---|---|---|
| `title` | string | 原始题干 |
| `type` | string | `single`、`multiple`、`judgement`、`completion` |
| `options` | string | 原始选项文字，按行分隔 |
| `images` | string[] | 图片 Base64 Data URL 数组。必须是数组；无图片为 `[]` |
| `suggestion_title` | string | 图片优化题干，含 `[图片1]`、`[图片2]` 标记 |
| `suggestion_options` | string | 图片优化选项，保留图片对应的序号 |

示例（图片的数据内容已省略，实际必须提供有效 PNG/JPEG/GIF/WebP）：

```json
{
  "title": "图中的颜色是？",
  "type": "single",
  "options": "A. 蓝色\nB. 红色",
  "images": ["data:image/png;base64,<BASE64_IMAGE_CONTENT>"],
  "suggestion_title": "[图片1] 中的颜色是？",
  "suggestion_options": "A. 蓝色\nB. 红色"
}
```

上述 `[图片1]` 与 `images[0]` 对应。服务会验证图片格式，限制最多 **10 张**、单张不超过 **8 MiB**、总解码字节不超过 **20 MiB**。不会下载题目给出的任意外部图片地址，避免防盗链或服务端请求伪造（SSRF）风险。图像内容及占位文本参与答案缓存指纹，避免不同图片共享错误答案。

纯文字题同样使用 POST，传 `images: []` 即可；旧 GET 接口仍支持纯文本，但不支持多模态图片。

### 正常结果

```json
{
  "code": 1,
  "question": "图中的颜色是？",
  "answer": "蓝色"
}
```

AI 或参数错误会返回 `code: 0` 和 `msg`，格式错误的图片可返回 HTTP 400。GPT 的答案不保证正确。

## OCS AnswererWrapper 模板

直接使用 [ocs_config_siwc_example.json](ocs_config_siwc_example.json)：

- `method: "post"`
- `type: "GM_xmlhttpRequest"`
- `headers` 包含 `X-Access-Token` 和 `Content-Type: application/json`
- `data.images` 必须是字段 handler：`{"handler": "return (env) => env.images || []"}`，不能用字符串把整个数组展开
- `suggestion_title` 和 `suggestion_options` 也通过 handler 取值
- `handler` 将 `{code,question,answer}` 转换为 `[question,answer]`

**OCS 配置界面的连接检测是无自定义请求头的 HEAD 探测**，返回 204 是正常的。独立检验真正的答题 POST 时仍须带 `X-Access-Token`。

## 环境变量

| 参数 | 默认值 | 用途 |
|---|---|---|
| `AUTH_MODE` | `api_key` | `siwc` 或 `api_key`，不可使用其他值 |
| `SIWC_AUTH_FILE` | `~/.config/ocsjs-ai-answer-service/siwc.json` | 可选的 OAuth 凭据存储路径 |
| `OPENAI_API_KEY` | 空 | 仅 API Key 模式需要 |
| `OPENAI_API_BASE` | `https://api.openai.com/v1` | 上游 API 地址 |
| `OPENAI_MODEL` | `gpt-6.1-sol` | 模型 ID，以当前账户可用列表为准 |
| `OPENAI_API_MODE` | `responses` | SIWC 仅支持 `responses`，API Key 可使用兼容的 `chat_completions` |
| `OPENAI_REASONING_EFFORT` | `medium` | 推理等级，支持范围取决于模型 |
| `ACCESS_TOKEN` | 空 | 本地接口调用密钥；SIWC 下必需 |
| `HOST` | `127.0.0.1` | 监听地址，容器内部可能要设 `0.0.0.0` |
| `PORT` | `5000` | 监听端口 |
| `ENABLE_CACHE` | `True` | 是否开启答案缓存 |
| `CACHE_EXPIRATION` | `86400` | 缓存过期时间（秒） |

SIWC 模式始终使用流式 Responses API 和 `store=false`，不发送旧版不兼容的 `max_output_tokens` 或 `service_tier` 参数；API Key 模式可以单独配置。

## 排查

- **配置界面 403**：检查服务是否为已支持 HEAD 204 的版本；不要把本地 Token 放到 URL 查询参数里。
- **真实答题 403**：检查请求头里的 `X-Access-Token` 是否与 `.env` 一致。
- **官方接口 401**：确保已通过 `siwc_login.py` 独立授权，并使用 `AUTH_MODE=siwc`；如令牌过期或撤销则重新授权。
- **图片题失败**：确认 OCS 图片题优化已开启、下载图片成功、POST 包含 `images` 数组。
- **模型找不到**：使用 `.venv/bin/python siwc_models.py` 查询当前账号的可用模型。
