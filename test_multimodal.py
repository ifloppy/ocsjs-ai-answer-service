"""Local regressions for October 2026 OCS imageOptimize contract."""
import base64
import logging
import json
import struct
import unittest
import zlib
from unittest import mock

import app as service
from multimodal import (
    ImagePayloadError, MAX_IMAGES, normalize_images,
    build_responses_input, image_fingerprint,
)


def png_data_url(color=(255, 0, 0)):
    def chunk(tag, data):
        return struct.pack("!I", len(data)) + tag + data + struct.pack("!I", zlib.crc32(tag + data) & 0xffffffff)
    scan = b"\0" + bytes(color)
    raw = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack("!2I5B", 1, 1, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(scan))
           + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(raw).decode()


class ImageFormatTests(unittest.TestCase):
    def test_order_and_responses_payload(self):
        images = normalize_images([png_data_url((255,0,0)), png_data_url((0,0,255))])
        payload = build_responses_input("哪张是蓝色？A.[图片1] B.[图片2]", images)
        self.assertEqual(len(payload[0]["content"]), 3)
        self.assertEqual(payload[0]["content"][0]["type"], "input_text")
        self.assertEqual(payload[0]["content"][1]["image_url"], images[0])
        self.assertEqual(payload[0]["content"][2]["image_url"], images[1])
        self.assertNotEqual(image_fingerprint(images), image_fingerprint(images[::-1]))

    def test_image_limit_and_invalid_input(self):
        with self.assertRaises(ImagePayloadError):
            normalize_images("not-an-array")
        with self.assertRaises(ImagePayloadError):
            normalize_images([png_data_url()] * (MAX_IMAGES + 1))
        with self.assertRaises(ImagePayloadError):
            normalize_images(["https://example.com/pic.png"])
        with self.assertRaises(ImagePayloadError):
            normalize_images(["data:image/png;base64,AAABBB"])

    def test_mislabelled_jpeg_is_fixed(self):
        jpeg_raw = b"\xff\xd8\xff\xe0" + bytes(32)
        payload = ["data:image/png;base64," + base64.b64encode(jpeg_raw).decode()]
        result = normalize_images(payload)
        self.assertTrue(result[0].startswith("data:image/jpeg;base64,"))


class EndToEndLocalTests(unittest.TestCase):
    def setUp(self):
        self.client = service.app.test_client()
        if service.cache:
            service.cache.clear()

    def test_ocs_json_post_with_images(self):
        captured = {}
        def fake_answer(prompt, images=None):
            captured.update(prompt=prompt, images=images)
            return "蓝色"
        with mock.patch.object(service, "generate_ai_answer", side_effect=fake_answer):
            payload = {
                "title": "图中是什么颜色？ https://example.org/pic.png",
                "options": "A. 蓝色\nB. 红色",
                "type": "single",
                "suggestion_title": "图中是什么颜色？ [图片1]",
                "suggestion_options": "A. 蓝色\nB. 红色",
                "images": [png_data_url((0, 0, 255))],
            }
            response = self.client.post(
                "/api/search", json=payload,
                headers={"X-Access-Token": service.Config.ACCESS_TOKEN},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["code"], 1)
            self.assertEqual(response.json["answer"], "蓝色")
            self.assertIn("[图片1]", captured["prompt"])
            self.assertEqual(len(captured["images"]), 1)
            self.assertEqual(response.json["question"], payload["title"])

    def test_same_title_distinct_image_not_reuse_cache(self):
        with mock.patch.object(service, "generate_ai_answer", side_effect=["红色", "蓝色"]) as model:
            results = []
            for color in ((255, 0, 0), (0, 0, 255)):
                response = self.client.post(
                    "/api/search",
                    json={"title": "这张图是什么颜色？", "type": "single",
                          "options": "A. 红色\nB. 蓝色",
                          "images": [png_data_url(color)],
                          "suggestion_title": "这张图 [图片1] 是什么颜色？"},
                    headers={"X-Access-Token": service.Config.ACCESS_TOKEN},
                )
                self.assertEqual(response.status_code, 200)
                results.append(response.json["answer"])
            self.assertEqual(results, ["红色", "蓝色"])
            self.assertEqual(model.call_count, 2)

    def test_ocs_v5_probe_head_without_token(self):
        """OCS 5.1.0 HEAD connectivity probe omits all configured headers."""
        response = self.client.head("/api/search?t=12345")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.data, b"")

    def test_ocs_probe_does_not_weaken_actual_auth(self):
        """Search still rejects missing tokens, including POST with real data."""
        get_response = self.client.get("/api/search?title=test")
        self.assertEqual(get_response.status_code, 403)
        post_response = self.client.post(
            "/api/search",
            json={"title": "test", "options": "A. yes", "type": "single", "images": []},
        )
        self.assertEqual(post_response.status_code, 403)

    def test_dashboard_requires_explicit_auth(self):
        self.assertEqual(self.client.get("/dashboard").status_code, 401)
        self.assertEqual(
            self.client.get("/dashboard").headers.get("Cache-Control"), "no-store"
        )
        token = service.Config.ACCESS_TOKEN
        basic = base64.b64encode(("ocs:" + token).encode()).decode()
        response = self.client.get(
            "/dashboard", headers={"Authorization": "Basic " + basic}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        # Basic auth is never accepted by the model-calling endpoint.
        self.assertEqual(
            self.client.post(
                "/api/search",
                json={"title": "untrusted"},
                headers={"Authorization": "Basic " + basic},
            ).status_code, 403
        )

    def test_token_in_query_is_rejected(self):
        self.assertEqual(
            self.client.get(
                "/api/search", query_string={
                    "title": "test", "token": service.Config.ACCESS_TOKEN
                },
            ).status_code, 403
        )

    def test_no_arbitrary_browser_cors(self):
        response = self.client.get(
            "/api/health", headers={"Origin": "https://example.org"}
        )
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_private_question_and_errors_not_logged_or_returned(self):
        with mock.patch.object(
            service, "generate_ai_answer",
            side_effect=ValueError("test-private-prompt-content"),
        ):
            with self.assertLogs(service.logger, level=logging.INFO) as logs:
                response = self.client.post(
                    "/api/search",
                    json={"title": "test-private-question-content"},
                    headers={"X-Access-Token": service.Config.ACCESS_TOKEN},
                )
        self.assertEqual(response.status_code, 502)
        combined = " ".join(logs.output) + response.get_data(as_text=True)
        self.assertNotIn("test-private-question-content", combined)
        self.assertNotIn("test-private-prompt-content", combined)

    def test_get_legacy_compatibility(self):
        with mock.patch.object(service, "generate_ai_answer", return_value="北京") as model:
            response = self.client.get("/api/search",query_string={"title":"中国首都","options":"A. 北京","type":"single"},headers={"X-Access-Token":service.Config.ACCESS_TOKEN})
            self.assertEqual(response.json["answer"], "北京")
            self.assertEqual(model.call_args.args[1], [])

    def test_rejects_payload_without_image_array(self):
        response = self.client.post("/api/search",
            json={"title":"测试图片题","images":"data:image/png;base64,xxx"},
            headers={"X-Access-Token":service.Config.ACCESS_TOKEN})
        self.assertEqual(response.status_code,400)


class AnswerFormatTests(unittest.TestCase):
    def test_choice_letters_only(self):
        from utils import extract_answer
        self.assertEqual(extract_answer("A、C", "multiple"), "A#C")
        self.assertEqual(extract_answer("AC", "multiple"), "A#C")
        self.assertEqual(extract_answer("答案：A, C", "multiple"), "A#C")
        self.assertEqual(extract_answer("A#C", "multiple"), "A#C")
        self.assertEqual(extract_answer("China", "multiple"), "China")
        self.assertEqual(extract_answer("中国#世界", "multiple"), "中国#世界")
        self.assertEqual(extract_answer("北京", "single"), "北京")


if __name__ == "__main__":
    unittest.main()
