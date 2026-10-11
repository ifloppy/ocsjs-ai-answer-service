"""Official Sign in with ChatGPT (SIWC) credential storage and renewal.

This module never reads Codex CLI credentials. The authenticated app receives
its own OAuth grants through an explicit user consent flow.
"""
import fcntl
import json
import os
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

AUTH_BASE = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
TOKEN_URL = AUTH_BASE + "/api/accounts/oauth/token"
DEFAULT_FILE = "~/.config/ocsjs-ai-answer-service/siwc.json"
REQUIRED_SCOPE = "chatgpt.tokens.use.direct"


class SIWCError(RuntimeError):
    pass


def credentials_path():
    return Path(os.getenv("SIWC_AUTH_FILE", DEFAULT_FILE)).expanduser()


def read_credentials(path=None):
    path = Path(path or credentials_path())
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SIWCError(
            "尚未完成 ChatGPT 订阅共享授权。请在项目目录运行 python siwc_login.py"
        ) from exc


def write_private_json(path, data):
    """Atomic mode-0600 write. Tokens must never go into source control."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=".auth-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def post_token(data):
    body = urlencode(data).encode("utf-8")
    req = Request(TOKEN_URL, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }, method="POST")
    try:
        with urlopen(req, timeout=20) as response:
            return json.load(response)
    except HTTPError as exc:
        # Never put token endpoint response bodies in exceptions or logs.
        raise SIWCError(f"ChatGPT OAuth token endpoint HTTP {exc.code}，请重新登录授权") from exc
    except URLError as exc:
        raise SIWCError("无法连接 ChatGPT OAuth 认证服务") from exc


def authorized_scopes(creds):
    scopes = creds.get("scopes", [])
    if isinstance(scopes, str):
        scopes = scopes.split()
    return REQUIRED_SCOPE in scopes and "resource.invoke" in scopes


def access_token():
    """Return a plan-usage token, renewing shortly before expiry.

    A filesystem lock serializes the rotating refresh token among processes.
    """
    path = credentials_path()
    creds = read_credentials(path)
    if not authorized_scopes(creds):
        raise SIWCError(
            "ChatGPT 登录成功，但未授权订阅额度。请重新执行 python siwc_login.py"
        )
    if creds.get("access_token") and float(creds.get("expires_at", 0)) > time.time() + 120:
        return creds["access_token"]

    lock_path = path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        with os.fdopen(descriptor, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            creds = read_credentials(path)
            if creds.get("access_token") and float(creds.get("expires_at", 0)) > time.time() + 120:
                return creds["access_token"]
            if not creds.get("refresh_token") or not creds.get("client_id"):
                raise SIWCError("缺少有效的刷新令牌，请重新执行 python siwc_login.py")
            refreshed = post_token({
                "grant_type": "refresh_token",
                "client_id": creds["client_id"],
                "refresh_token": creds["refresh_token"],
                "resource": RESOURCE,
            })
            if not refreshed.get("access_token") or not refreshed.get("refresh_token"):
                raise SIWCError("OAuth 刷新响应缺少必要令牌，请重新登录")
            scopes = refreshed.get("scope", " ".join(creds["scopes"])).split()
            updated = dict(creds)
            updated.update({
                "access_token": refreshed["access_token"],
                "refresh_token": refreshed["refresh_token"],
                "scopes": scopes,
                "expires_at": time.time() + int(refreshed.get("expires_in", 3600)),
                "saved_at": time.time(),
            })
            if not authorized_scopes(updated):
                raise SIWCError("刷新后的 OAuth 令牌失去了订阅共享权限")
            write_private_json(path, updated)
            return updated["access_token"]
    finally:
        # fd is closed by the context manager on normal paths; no token is logged.
        pass
