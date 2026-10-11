"""Explicit live-service smoke test (never runs automatically in unit tests).

Run: python test_service.py             # only free health/security probes
Run: python test_service.py --inference # spends one model request
"""
import argparse
import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dotenv import load_dotenv

load_dotenv()
DEFAULT_BASE = "http://127.0.0.1:5000"


def check_http(base, path, expected, method="GET", payload=None, token=None):
    headers = {}
    if token:
        headers["X-Access-Token"] = token
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = Request(
        base + path,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        method=method,
        headers=headers,
    )
    try:
        with urlopen(request, timeout=60) as response:
            status = response.status
            body = response.read()
    except HTTPError as exc:
        status = exc.code
        body = exc.read()
    assert status == expected, f"{method} {path}: expected HTTP {expected}, got {status}"
    return json.loads(body) if body else None


def main():
    parser = argparse.ArgumentParser(description="OCS AI 答题服务安全自检")
    parser.add_argument("--url", default=os.getenv("SERVICE_URL", DEFAULT_BASE))
    parser.add_argument("--inference", action="store_true", help="实际调用一次 GPT，会消耗模型额度")
    args = parser.parse_args()
    base = args.url.rstrip("/")
    token = os.getenv("ACCESS_TOKEN")
    health = check_http(base, "/api/health", 200)
    assert health["status"] == "ok"
    check_http(base, "/api/search?t=probe", 204, method="HEAD")
    if token:
        check_http(base, "/api/search", 403, method="POST", payload={"title": "未授权测试"})
        if args.inference:
            result = check_http(
                base, "/api/search", 200, method="POST", token=token,
                payload={"title": "中国的首都是哪里？", "type": "single",
                         "options": "A. 北京\nB. 上海", "images": [],
                         "suggestion_title": "", "suggestion_options": ""},
            )
            assert result.get("code") == 1
            print("模型答题成功，返回：", result["answer"])
    elif args.inference:
        parser.error("请求推理前必须设置本地 ACCESS_TOKEN")
    print("健康、连通性与鉴权检查通过。")


if __name__ == "__main__":
    main()
