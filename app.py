# -*- coding: utf-8 -*-
"""
EduBrain AI - 智能题库系统
基于 OpenAI API 的智能题库服务，提供兼容 OCS 接口的智能答题功能
作者：Lynn
版本：1.2.0
"""
from flask import Flask, request, jsonify, make_response, render_template
import time
import logging
import openai
import hmac
import hashlib
from datetime import datetime
from pathlib import Path

from config import Config
from utils import SimpleCache, format_answer_for_ocs, parse_question_and_options, extract_answer
from siwc_auth import access_token as get_siwc_access_token
from multimodal import (
    ImagePayloadError, normalize_images, build_responses_input, image_fingerprint,
)

# 配置日志
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('ai_answer_service')
APP_VERSION = "1.2.0"

# 初始化应用
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 28 * 1024 * 1024  # 避免图片请求耗尽内存
# Tampermonkey 的 GM_xmlhttpRequest 不依赖浏览器 CORS；不向任意网页开放本地 API。

# 初始化缓存
cache = SimpleCache(Config.CACHE_EXPIRATION) if Config.ENABLE_CACHE else None

SUPPORTED_API_MODES = {"responses", "chat_completions"}
if Config.OPENAI_API_MODE not in SUPPORTED_API_MODES:
    raise ValueError(
        "OPENAI_API_MODE 必须是 'responses' 或 'chat_completions'"
    )

# 验证OpenAI API密钥
if Config.SIWC_PLAN_AUTH and not Config.ACCESS_TOKEN:
    raise ValueError("SIWC 模式必须设置独立的 ACCESS_TOKEN，以免网页消耗你的 ChatGPT 订阅额度")
if not Config.SIWC_PLAN_AUTH and not Config.OPENAI_API_KEY:
    logger.critical("API Key 模式未配置 OPENAI_API_KEY")
    raise ValueError("请设置 OPENAI_API_KEY 或使用 AUTH_MODE=siwc")

# 初始化OpenAI客户端
if Config.SIWC_PLAN_AUTH and Config.OPENAI_API_MODE != "responses":
    raise ValueError("官方 ChatGPT 订阅共享仅支持 Responses API")

client = None if Config.SIWC_PLAN_AUTH else openai.OpenAI(
    api_key=Config.OPENAI_API_KEY,
    base_url=Config.OPENAI_API_BASE,
    max_retries=2
)

# 问答记录存储（实际应用中可以使用数据库）
qa_records = []
MAX_RECORDS = 100  # 最多保存100条记录
start_time = time.time()

SYSTEM_INSTRUCTIONS = (
    "你是一个专业的考试答题助手。请直接回答答案，不要解释。"
    "选择题只回答选项的内容(如：地球)；多选题用#号分隔答案,只回答选项的内容(如中国#世界#地球)；"
    "判断题只回答: 正确/对/true/√ 或 错误/错/false/×；填空题直接给出答案。"
    "遇到 [图片N] 时，编号指向输入中第 N 张图片；图片可能位于题干或选项中。"
    "如果需要选择图片选项，优先返回对应选项的完整文字/编号，以便 OCS 正确匹配。"
)


def _model_supports_reasoning_effort(model):
    """GPT-5/6 推理模型支持 reasoning effort 参数。"""
    normalized_model = model.lower()
    return normalized_model.startswith(("gpt-5", "gpt-6"))


def generate_ai_answer(prompt, images=None):
    """根据配置调用 Responses API 或旧版 Chat Completions API。"""
    model = Config.OPENAI_MODEL
    is_reasoning_model = _model_supports_reasoning_effort(model)
    effort = Config.OPENAI_REASONING_EFFORT
    request_client = (
        openai.OpenAI(api_key=get_siwc_access_token(), base_url=Config.OPENAI_API_BASE, max_retries=0)
        if Config.SIWC_PLAN_AUTH else client
    )

    if Config.OPENAI_API_MODE == "responses":
        params = {
            "model": model,
            "instructions": SYSTEM_INSTRUCTIONS,
            "input": build_responses_input(prompt, images or []) if (Config.SIWC_PLAN_AUTH or images) else prompt,
            "store": False if Config.SIWC_PLAN_AUTH else Config.OPENAI_STORE,
            "stream": True if Config.SIWC_PLAN_AUTH else Config.OPENAI_STREAM,
        }
        # 官方订阅共享接口不允许 max_output_tokens；服务等级也由计划决定。
        if not Config.SIWC_PLAN_AUTH:
            params["max_output_tokens"] = Config.MAX_OUTPUT_TOKENS
            if Config.OPENAI_SERVICE_TIER:
                params["service_tier"] = Config.OPENAI_SERVICE_TIER
        if effort and is_reasoning_model:
            params["reasoning"] = {"effort": effort}

        if not params["stream"]:
            response = request_client.responses.create(**params)
            return (response.output_text or "").strip()

        output_chunks = []
        completed = False
        with request_client.responses.create(**params) as response:
            for event in response:
                event_type = getattr(event, "type", "")
                if event_type == "response.output_text.delta":
                    output_chunks.append(event.delta)
                elif event_type == "response.completed":
                    completed = True
                elif event_type == "response.failed":
                    error = getattr(getattr(event, "response", None), "error", None)
                    code = getattr(error, "code", None) or "unknown_error"
                    message = getattr(error, "message", None) or code
                    raise RuntimeError(f"OpenAI Responses API 调用失败: {message}")
                elif event_type == "response.incomplete":
                    details = getattr(
                        getattr(event, "response", None), "incomplete_details", None
                    )
                    reason = getattr(details, "reason", None) or "unknown_reason"
                    raise RuntimeError(f"OpenAI Responses API 响应不完整: {reason}")
                elif event_type == "error":
                    raise RuntimeError(
                        f"OpenAI Responses API 流式调用失败: "
                        f"{getattr(event, 'message', '未知错误')}"
                    )

        if not completed:
            raise RuntimeError("OpenAI Responses API 流在响应完成前结束")
        return "".join(output_chunks).strip()

    if images:
        raise ValueError("图片题请使用 Responses API（OPENAI_API_MODE=responses）")
    messages = [
        {"role": "system", "content": SYSTEM_INSTRUCTIONS},
        {"role": "user", "content": prompt},
    ]
    params = {"model": model, "messages": messages}

    if is_reasoning_model:
        params["max_completion_tokens"] = Config.MAX_OUTPUT_TOKENS
        if effort:
            params["reasoning_effort"] = effort
    else:
        params["max_tokens"] = Config.MAX_TOKENS
        params["temperature"] = Config.TEMPERATURE

    response = request_client.chat.completions.create(**params)
    return (response.choices[0].message.content or "").strip()

def verify_access_token(req, *, allow_basic=False):
    """Use the local secret in a header; dashboard also supports HTTP Basic.

    Never accept credentials in URLs, where browser history and proxy logs leak them.
    """
    if not Config.ACCESS_TOKEN:
        return True
    token = req.headers.get("X-Access-Token")
    if not token and allow_basic:
        credentials = req.authorization
        if (credentials and credentials.type.lower() == "basic"
                and credentials.username == "ocs"):
            token = credentials.password
    return bool(token) and hmac.compare_digest(str(token), Config.ACCESS_TOKEN)

@app.route('/api/search', methods=['GET', 'POST', 'HEAD'])
def search():
    """
    处理OCS发送的搜索请求，使用OpenAI API生成答案
    GET请求: 从URL参数获取问题
    POST请求: 从请求体获取问题
    
    参数:
        title: 问题内容
        type: 问题类型 (single-单选, multiple-多选, judgement-判断, completion-填空)
        options: 选项内容
        HEAD: OCS 5.1.0 题库连通性探测不附带配置里的请求头；
              只返回空 204，不进行推理，不开放 GET/POST 鉴权。
        images: OCS 图片优化后的 Base64 Data URL 数组（POST JSON）
        suggestion_title/suggestion_options: 带 [图片N] 占位符的题干与选项
        
    返回:
        成功: {'code': 1, 'question': '问题', 'answer': 'AI生成的答案'}
        失败: {'code': 0, 'msg': '错误信息'}
    """
    # OCS 5.1.0 会对接口直接发 HEAD 探测（不附带题库配置中的 headers）。
    # 探测只验证本地服务可达，返回无内容 204；GET/POST 继续严格鉴权。
    if request.method == 'HEAD':
        return '', 204

    start_time = time.time()

    # 验证访问令牌（如果配置了的话）
    if not verify_access_token(request):
        return jsonify({
            'code': 0,
            'msg': '无效的访问令牌'
        }), 403
    
    try:
        images = []
        suggestion_title = ''
        suggestion_options = ''
        # 根据请求方法获取问题内容
        if request.method == 'GET':
            question = request.args.get('title', '')
            question_type = request.args.get('type', '')
            options = request.args.get('options', '')
        else:  # POST
            content_type = request.headers.get('Content-Type', '')
            
            if 'application/json' in content_type:
                data = request.get_json() or {}
                if not isinstance(data, dict):
                    raise ValueError('POST JSON 必须是对象')
                images = normalize_images(data.get('images', []))
                suggestion_title = data.get('suggestion_title', '') or ''
                suggestion_options = data.get('suggestion_options', '') or ''
                question = data.get('title', '')
                question_type = data.get('type', '')
                options = data.get('options', '')
            else:
                # 处理表单数据
                question = request.form.get('title', '')
                question_type = request.form.get('type', '')
                options = request.form.get('options', '')
        
        if not isinstance(question, str) or not isinstance(options, str):
            raise ValueError('题干和选项必须是字符串')
        if not isinstance(suggestion_title, str) or not isinstance(suggestion_options, str):
            raise ValueError('suggestion_title 和 suggestion_options 必须是字符串')

        # 默认日志不应存储题目、选项、图片或访问密钥。
        question_id = hashlib.sha256(question.encode("utf-8")).hexdigest()[:12]
        logger.info("答题请求 id=%s type=%s images=%d", question_id, question_type, len(images))
        
        # 如果没有提供问题，返回错误
        if not question and not images:
            logger.warning("未提供问题内容")
            return jsonify({
                'code': 0,
                'msg': '未提供问题内容'
            })
        
        # 图片指纹参与缓存，避免图片不同但题干相同时错误命中。
        cache_options = options
        if images:
            cache_options += '\n[OCS_IMAGE_SHA256]:' + image_fingerprint(images, suggestion_title, suggestion_options)
        if Config.ENABLE_CACHE:
            cached_answer = cache.get(question, question_type, cache_options)
            if cached_answer:
                logger.info(f"从缓存获取答案 (耗时: {time.time() - start_time:.2f}秒)")
                return jsonify(format_answer_for_ocs(question, cached_answer))
        
        # 构建发送给OpenAI的提示
        # OCS 的 [图片N] 标记对应 images 第 N 张，不能使用原 URL 替代。
        ai_title = (suggestion_title or question) if images else question
        ai_options = (suggestion_options or options) if images else options
        prompt = parse_question_and_options(ai_title, ai_options, question_type)
        
        # 调用OpenAI API
        # 调用配置的 OpenAI API 并获取文本答案
        ai_answer = generate_ai_answer(prompt, images)
        
        # 处理答案格式
        processed_answer = extract_answer(ai_answer, question_type)
        
        # 保存到缓存
        if Config.ENABLE_CACHE:
            cache.set(question, processed_answer, question_type, cache_options)
        
        # 保存问答记录
        current_time = datetime.now()
        qa_records.append({
            'time': current_time.strftime('%Y-%m-%d %H:%M:%S'),
            'timestamp': current_time.isoformat(),
            'question': question,
            'type': question_type,
            'options': options,
            'image_count': len(images),
            'answer': processed_answer
        })
        if len(qa_records) > MAX_RECORDS:
            qa_records.pop(0)
        
        # 记录处理时间
        process_time = time.time() - start_time
        logger.info(f"问题处理完成 (耗时: {process_time:.2f}秒)")
        
        # 返回符合OCS格式的响应
        return jsonify(format_answer_for_ocs(question, processed_answer))
    
    except ImagePayloadError as e:
        return jsonify({'code': 0, 'msg': str(e)}), 400
    except Exception as exc:
        # 上游错误正文可能包含私人题目/图片 URL，切勿写入日志或响应。
        logger.error(
            "答题失败：%s (HTTP %s)",
            type(exc).__name__, getattr(exc, "status_code", "n/a")
        )
        return jsonify({
            "code": 0,
            "msg": "模型调用失败。请检查服务日志中的错误类别和上游状态码。",
        }), 502

@app.route('/api/health', methods=['GET'])
def health_check():
    """健康检查接口"""
    return jsonify({
        'status': 'ok',
        'message': 'AI题库服务运行正常',
        'version': APP_VERSION,
        'cache_enabled': Config.ENABLE_CACHE,
        'model': Config.OPENAI_MODEL,
        'api_mode': Config.OPENAI_API_MODE,
        'reasoning_effort': Config.OPENAI_REASONING_EFFORT or None,
        'store': Config.OPENAI_STORE,
        'stream': Config.OPENAI_STREAM,
        'service_tier': Config.OPENAI_SERVICE_TIER or None,
        'auth_mode': 'siwc_plan' if Config.SIWC_PLAN_AUTH else 'api_key'
    })

@app.route('/api/cache/clear', methods=['POST'])
def clear_cache():
    """清除缓存接口"""
    # 验证访问令牌
    if not verify_access_token(request):
        return jsonify({
            'success': False,
            'message': '无效的访问令牌'
        }), 403
    
    if not Config.ENABLE_CACHE:
        return jsonify({
            'success': False,
            'message': '缓存未启用'
        })
    
    cache.clear()
    return jsonify({
        'success': True,
        'message': '缓存已清除'
    })

@app.route('/api/stats', methods=['GET'])
def get_stats():
    """获取服务统计信息"""
    # 验证访问令牌
    if not verify_access_token(request):
        return jsonify({
            'success': False,
            'message': '无效的访问令牌'
        }), 403
    
    stats = {
        'version': APP_VERSION,
        'uptime': time.time() - start_time,
        'model': Config.OPENAI_MODEL,
        'api_mode': Config.OPENAI_API_MODE,
        'reasoning_effort': Config.OPENAI_REASONING_EFFORT or None,
        'store': Config.OPENAI_STORE,
        'stream': Config.OPENAI_STREAM,
        'service_tier': Config.OPENAI_SERVICE_TIER or None,
        'auth_mode': 'siwc_plan' if Config.SIWC_PLAN_AUTH else 'api_key',
        'cache_enabled': Config.ENABLE_CACHE,
        'cache_size': len(cache.cache) if Config.ENABLE_CACHE else 0,
        'qa_records_count': len(qa_records)
    }
    
    return jsonify(stats)

@app.route('/dashboard', methods=['GET'])
def dashboard():
    """Only the local service owner can view the in-memory question history."""
    if not verify_access_token(request, allow_basic=True):
        return (
            "需要通过 HTTP Basic 登录查看本地答题记录。",
            401,
            {"WWW-Authenticate": 'Basic realm="OCS Local Dashboard", charset="UTF-8"',
             "Cache-Control": "no-store"},
        )
    uptime_seconds = time.time() - start_time
    days = int(uptime_seconds // 86400)
    hours = int((uptime_seconds % 86400) // 3600)
    minutes = int((uptime_seconds % 3600) // 60)
    uptime_str = f"{days}天{hours}小时{minutes}分钟"
    
    response = make_response(render_template(
        'dashboard.html',
        version=APP_VERSION,
        cache_enabled=Config.ENABLE_CACHE,
        cache_size=len(cache.cache) if Config.ENABLE_CACHE else 0,
        model=Config.OPENAI_MODEL,
        uptime=uptime_str,
        records=qa_records
    ))
    response.headers["Cache-Control"] = "no-store"
    return response

@app.route('/', methods=['GET'])
def index():
    """首页仅展示公开配置，不含本地密钥。"""
    public_config = Path(__file__).with_name('ocs_config_siwc_example.json').read_text(encoding='utf-8')
    return render_template('index.html', ocs_config_example=public_config)

@app.route('/docs', methods=['GET'])
def docs():
    """API文档页面"""
    with open('api_docs.md', 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 使用markdown库将文档转换为HTML（需要安装：pip install markdown）
    try:
        import markdown
        html_content = markdown.markdown(content, extensions=['tables'])
        
        return f"""
        <html>
            <head>
                <title>AI题库服务 - API文档</title>
                <style>
                    body {{ font-family: Arial, sans-serif; margin: 40px; line-height: 1.6; }}
                    h1, h2, h3 {{ color: #2c3e50; }}
                    .container {{ max-width: 800px; margin: 0 auto; }}
                    code {{ background: #e0e0e0; padding: 2px 4px; border-radius: 3px; }}
                    pre {{ background: #f4f4f4; padding: 10px; border-radius: 4px; overflow-x: auto; }}
                    table {{ border-collapse: collapse; width: 100%; }}
                    th, td {{ border: 1px solid #ddd; padding: 8px; }}
                    th {{ background-color: #f4f4f4; }}
                </style>
            </head>
            <body>
                <div class="container">
                    {html_content}
                </div>
            </body>
        </html>
        """
    except ImportError:
        # 如果没有安装markdown库，则返回纯文本
        return f"""
        <html>
            <head>
                <title>AI题库服务 - API文档</title>
                <style>
                    body {{ font-family: Arial, sans-serif; margin: 40px; line-height: 1.6; }}
                    h1 {{ color: #333; }}
                    .container {{ max-width: 800px; margin: 0 auto; }}
                    pre {{ background: #f4f4f4; padding: 10px; border-radius: 4px; overflow-x: auto; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>AI题库服务 - API文档</h1>
                    <pre>{content}</pre>
                </div>
            </body>
        </html>
        """

if __name__ == '__main__':
    # 开启应用
    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG)
