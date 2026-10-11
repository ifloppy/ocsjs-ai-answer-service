"""List models eligible for the current SIWC-authorized ChatGPT account."""
import json
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from siwc_auth import SIWCError, access_token


def main():
    token = access_token()
    req = Request(
        "https://api.openai.com/v1/models",
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
    )
    try:
        with urlopen(req, timeout=20) as response:
            data = json.load(response)
    except HTTPError as exc:
        raise SIWCError(f"模型列表请求失败 (HTTP {exc.code})；请检查订阅共享权限") from exc
    models = data.get("models", data.get("data", []))
    if not isinstance(models, list):
        raise SIWCError("模型列表返回结构不符合预期")
    for model in models:
        if model.get("visibility") not in (None, "list"):
            continue
        slug = model.get("slug") or model.get("id")
        if slug:
            print(f"{slug} | {model.get('display_name', slug)}")


if __name__ == "__main__":
    try:
        main()
    except (SIWCError, OSError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1)
