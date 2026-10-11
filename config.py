# -*- coding: utf-8 -*-
"""服务配置。

只支持两种独立认证方式：
- AUTH_MODE=siwc：官方 Sign in with ChatGPT，OAuth 刷新由 siwc_auth.py 维护。
- AUTH_MODE=api_key：常规 OpenAI API Key 或兼容 API 服务。

不读取或转用 Codex CLI 的个人登录凭据。
"""
import os

from dotenv import load_dotenv

load_dotenv(override=True)


class Config:
    HOST = os.getenv("HOST", "127.0.0.1")
    PORT = int(os.getenv("PORT", "5000"))
    DEBUG = os.getenv("DEBUG", "False").strip().lower() == "true"

    AUTH_MODE = os.getenv("AUTH_MODE", "api_key").strip().lower()
    if AUTH_MODE not in {"siwc", "api_key"}:
        raise ValueError("AUTH_MODE 只能是 siwc 或 api_key")
    SIWC_PLAN_AUTH = AUTH_MODE == "siwc"
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") if not SIWC_PLAN_AUTH else None

    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-6.1-sol")
    OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1").strip()
    OPENAI_API_MODE = os.getenv("OPENAI_API_MODE", "responses").strip().lower()
    OPENAI_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "medium").strip().lower()
    OPENAI_STORE = os.getenv("OPENAI_STORE", "False").strip().lower() == "true"
    OPENAI_STREAM = os.getenv("OPENAI_STREAM", "True").strip().lower() == "true"
    OPENAI_SERVICE_TIER = os.getenv("OPENAI_SERVICE_TIER", "").strip().lower()

    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    # 本地接口的密钥，不是 OpenAI 凭据。
    ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

    MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", os.getenv("MAX_TOKENS", "500")))
    MAX_TOKENS = MAX_OUTPUT_TOKENS
    TEMPERATURE = float(os.getenv("TEMPERATURE", "0.7"))

    ENABLE_CACHE = os.getenv("ENABLE_CACHE", "True").strip().lower() == "true"
    CACHE_EXPIRATION = int(os.getenv("CACHE_EXPIRATION", "86400"))
