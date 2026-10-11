"""Offline regression tests for the SIWC bridge (never touch ChatGPT or Codex)."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import siwc_auth


class SIWCAuthTests(unittest.TestCase):
    def test_rejects_missing_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            auth = Path(folder) / "auth.json"
            siwc_auth.write_private_json(auth, {
                "access_token": "dummy",
                "scopes": ["openid", "resource.invoke"],
                "expires_at": time.time() + 3600,
            })
            with mock.patch.dict(os.environ, {"SIWC_AUTH_FILE": str(auth)}):
                with self.assertRaisesRegex(siwc_auth.SIWCError, "订阅额度"):
                    siwc_auth.access_token()

    def test_refresh_rotating_token(self):
        with tempfile.TemporaryDirectory() as folder:
            auth = Path(folder) / "auth.json"
            siwc_auth.write_private_json(auth, {
                "access_token": "old",
                "refresh_token": "old-refresh",
                "client_id": "oaiapp_test",
                "scopes": ["resource.invoke", "chatgpt.tokens.use.direct"],
                "expires_at": time.time() - 1,
            })
            with mock.patch.dict(os.environ, {"SIWC_AUTH_FILE": str(auth)}), mock.patch.object(
                siwc_auth, "post_token",
                return_value={
                    "access_token": "new", "refresh_token": "new-refresh",
                    "expires_in": 3600,
                    "scope": "resource.invoke chatgpt.tokens.use.direct",
                },
            ) as post:
                self.assertEqual(siwc_auth.access_token(), "new")
                self.assertEqual(siwc_auth.access_token(), "new")
                post.assert_called_once()
                self.assertEqual(json.loads(auth.read_text())["refresh_token"], "new-refresh")
                self.assertEqual(auth.stat().st_mode & 0o777, 0o600)


class ServiceRequestTests(unittest.TestCase):
    def test_siwc_inference_omits_unsupported_fields(self):
        import app as service
        from types import SimpleNamespace
        class FakeStream:
            def __enter__(self):
                return iter([
                    SimpleNamespace(type="response.output_text.delta", delta="北京"),
                    SimpleNamespace(type="response.completed"),
                ])
            def __exit__(self, *args):
                return False
        fake_client = mock.Mock()
        fake_client.responses.create.return_value = FakeStream()
        with mock.patch.object(service.Config, "SIWC_PLAN_AUTH", True), \
             mock.patch.object(service.Config, "OPENAI_API_MODE", "responses"), \
             mock.patch.object(service.Config, "OPENAI_STREAM", False), \
             mock.patch.object(service.Config, "OPENAI_STORE", True), \
             mock.patch.object(service.Config, "OPENAI_SERVICE_TIER", "fast"), \
             mock.patch.object(service, "get_siwc_access_token", return_value="test"), \
             mock.patch.object(service.openai, "OpenAI", return_value=fake_client):
            answer = service.generate_ai_answer("中国的首都？")
            self.assertEqual(answer, "北京")
            data = fake_client.responses.create.call_args.kwargs
            self.assertEqual(data["input"], [{"role": "user", "content": "中国的首都？"}])
            self.assertFalse(data["store"])
            self.assertTrue(data["stream"])
            self.assertNotIn("max_output_tokens", data)
            self.assertNotIn("service_tier", data)


class AuthModeRegressionTests(unittest.TestCase):
    """确保旧 Codex 凭据没有入口，同时保留正常 API Key 模式。"""

    @staticmethod
    def _load_config_in_isolation(overrides):
        import importlib.util
        path = Path(__file__).with_name("config.py")
        spec = importlib.util.spec_from_file_location("_isolated_config_test", path)
        module = importlib.util.module_from_spec(spec)
        # Tests must not read the user's private .env.
        with mock.patch("dotenv.load_dotenv", return_value=None), mock.patch.dict(
            os.environ, overrides, clear=True
        ):
            spec.loader.exec_module(module)
        return module.Config

    def test_api_key_mode_does_not_read_codex_file(self):
        cfg = self._load_config_in_isolation({
            "AUTH_MODE": "api_key", "OPENAI_API_KEY": "dummy-api-key",
            "CODEX_AUTH_FILE": "/does/not/exist",
            "OPENAI_OAUTH_ACCESS_TOKEN": "legacy-unused",
        })
        self.assertEqual(cfg.AUTH_MODE, "api_key")
        self.assertEqual(cfg.OPENAI_API_KEY, "dummy-api-key")
        self.assertFalse(hasattr(cfg, "CODEX_AUTH_FILE"))
        self.assertFalse(hasattr(cfg, "OPENAI_OAUTH_ACCESS_TOKEN"))

    def test_siwc_mode_never_uses_openai_api_key(self):
        cfg = self._load_config_in_isolation({
            "AUTH_MODE": "siwc", "OPENAI_API_KEY": "must-not-be-used",
            "CODEX_AUTH_FILE": "/does/not/exist",
        })
        self.assertTrue(cfg.SIWC_PLAN_AUTH)
        self.assertIsNone(cfg.OPENAI_API_KEY)

    def test_removed_legacy_auth_mode_fails_fast(self):
        with self.assertRaisesRegex(ValueError, "AUTH_MODE"):
            self._load_config_in_isolation({"AUTH_MODE": "legacy"})


if __name__ == "__main__":
    unittest.main()
