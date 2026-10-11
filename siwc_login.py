"""Interactive OpenAI-supported Sign in with ChatGPT for this local OSS app.

Run: python siwc_login.py
The login window is OpenAI's own auth page; this process only receives a
loopback OAuth callback. No Codex auth.json token is copied or reused.
"""
import base64
import hashlib
import json
import secrets
import sys
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen

from siwc_auth import (
    AUTH_BASE, RESOURCE, REQUIRED_SCOPE, SIWCError, credentials_path,
    post_token, read_credentials, write_private_json,
)

APP_NAME = "OCS-JS AI Answer Service"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"


def get_host_id():
    path = credentials_path().with_name("host.json")
    if path.is_file():
        host_id = json.loads(path.read_text(encoding="utf-8")).get("ext_agent_host_id")
        if host_id:
            return host_id
    host_id = "urn:uuid:" + str(uuid.uuid4())
    write_private_json(path, {"ext_agent_host_id": host_id})
    return host_id


def validate_id_token(id_token, client_id, nonce):
    try:
        import jwt
    except ImportError as exc:
        raise SIWCError("缺少 PyJWT[crypto]，请先安装项目依赖") from exc
    with urlopen(AUTH_BASE + "/.well-known/openid-configuration", timeout=15) as resp:
        discovery = json.load(resp)
    issuer = discovery["issuer"]
    jwks_uri = discovery["jwks_uri"]
    if not jwks_uri.startswith("https://auth.openai.com/"):
        raise SIWCError("OIDC JWKS 来源不符合预期")
    jwk_client = jwt.PyJWKClient(jwks_uri)
    public_key = jwk_client.get_signing_key_from_jwt(id_token).key
    claims = jwt.decode(
        id_token, public_key, algorithms=["RS256", "ES256"],
        audience=client_id, issuer=issuer,
        options={"require": ["iss", "aud", "sub", "exp", "iat"]},
        leeway=30,
    )
    if claims.get("nonce") != nonce:
        raise SIWCError("OIDC nonce 校验失败")
    return claims


def main():
    # Check cryptographic verification dependencies before opening the browser.
    # Otherwise users might finish authorization before discovering a missing lib.
    try:
        import jwt
        import cryptography
    except ImportError as exc:
        raise SIWCError(
            "缺少 OAuth 身份验证依赖。请使用项目虚拟环境："
            ".venv/bin/python siwc_login.py（必要时先运行 "
            ".venv/bin/python -m pip install -r requirements.txt）"
        ) from exc

    path = credentials_path()
    existing = None
    if path.exists():
        existing = read_credentials(path)
    client_id = existing.get("client_id") if existing else "dynamic_agent_client"
    if not client_id:
        client_id = "dynamic_agent_client"
    first_registration = client_id == "dynamic_agent_client"
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")

    callback = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlsplit(self.path)
            if url.path != "/auth/callback":
                self.send_error(404)
                return
            parameters = parse_qs(url.query)
            if parameters.get("state", [None])[0] != state:
                self.send_error(400, "OAuth state mismatch")
                return
            callback.update({key: values[0] for key, values in parameters.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write("<h2>ChatGPT 登录回调已收到</h2><p>可以返回终端查看结果。</p>".encode("utf-8"))

        def log_message(self, fmt, *args):
            # The callback URL contains an authorization code; never log it.
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 1
    redirect_uri = f"http://127.0.0.1:{server.server_port}/auth/callback"
    params = {
        "client_id": client_id,
        "ext_agent_host_id": get_host_id(),
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "resource": RESOURCE,
        "state": state,
        "nonce": nonce,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
    }
    if first_registration:
        params["agent_name_hint"] = APP_NAME

    authorize_url = AUTH_BASE + "/api/accounts/authorize?" + urlencode(params)
    print("正在通过系统浏览器打开 OpenAI 官方授权页（请手动确认授权）。", flush=True)
    if not webbrowser.open(authorize_url):
        # No tokens in this first-time URL. For an offline browser, retry on desktop.
        print("无法自动打开浏览器。请在图形桌面终端运行 python siwc_login.py", flush=True)
    try:
        deadline = time.monotonic() + 240
        while not callback and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()

    if not callback:
        raise SIWCError("等待浏览器授权超时，凭据未更改")
    if callback.get("error"):
        raise SIWCError("用户未授权 ChatGPT 订阅共享：" + callback["error"])
    code = callback.get("code")
    returned_client = callback.get("client_id")
    if not code:
        raise SIWCError("OAuth 回调没有授权码")
    if first_registration:
        if not returned_client or returned_client == "dynamic_agent_client":
            raise SIWCError("OAuth 回调未返回注册后的 client_id")
        client_id = returned_client
    elif returned_client and returned_client != client_id:
        raise SIWCError("OAuth 返回了不同的 client_id，已拒绝替换账户")
    result = post_token({
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
        "resource": RESOURCE,
    })
    if not result.get("id_token") or not result.get("access_token"):
        raise SIWCError("OAuth 响应缺少 identity/access token")
    claims = validate_id_token(result["id_token"], client_id, nonce)
    if existing and existing.get("subject") and claims["sub"] != existing["subject"]:
        raise SIWCError("登录的账户不是原来的 ChatGPT 账户，拒绝覆盖凭据")
    scopes = result.get("scope", "").split()
    if REQUIRED_SCOPE not in scopes or "resource.invoke" not in scopes:
        raise SIWCError("账户未授予 ChatGPT 订阅共享权限，未保存凭据")
    if not result.get("refresh_token"):
        raise SIWCError("OAuth 响应未包含 refresh_token，无法持续使用")

    write_private_json(path, {
        "client_id": client_id,
        "ext_agent_host_id": get_host_id(),
        "issuer": claims.get("iss"),
        "subject": claims["sub"],
        "email": claims.get("email", ""),
        "access_token": result["access_token"],
        "refresh_token": result["refresh_token"],
        "id_token": result["id_token"],
        "scopes": scopes,
        "expires_at": time.time() + int(result.get("expires_in", 3600)),
        "saved_at": time.time(),
    })
    print(f"授权成功。独立应用凭据已安全保存至 {path}", flush=True)
    print("请设置 AUTH_MODE=siwc 后启动服务。", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (SIWCError, OSError, ValueError) as exc:
        print(f"登录未完成：{exc}", file=sys.stderr)
        raise SystemExit(1)
