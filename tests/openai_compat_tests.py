import base64
import json
import tempfile
import time
import unittest
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

import server

from gateway.openai_compat import (
    OpenAICompatError,
    decode_video_id,
    encode_video_id,
    image_size_to_ratio,
    openai_error_payload,
    resolve_openai_model,
    task_to_video_object,
    video_size_to_ratio,
)


class OpenAICompatPrimitiveTests(unittest.TestCase):
    def test_cors_origin_normalization_rejects_malformed_configuration(self):
        self.assertEqual(
            server.normalize_cors_allowed_origins(
                [
                    " https://canvas.best/ ",
                    "https://canvas.best",
                    "http://localhost:3000",
                    "*",
                    "https://trusted.example/path",
                    123,
                ]
            ),
            ["https://canvas.best", "http://localhost:3000"],
        )
        self.assertEqual(server.normalize_cors_allowed_origins("https://canvas.best"), [])

    def test_video_ids_are_reversible_and_reject_invalid_values(self):
        self.assertEqual(encode_video_id(42), "video_42")
        self.assertEqual(decode_video_id("video_42"), 42)
        for value in ("42", "video_0", "video_-1", "video_nope", ""):
            with self.subTest(value=value):
                with self.assertRaises(OpenAICompatError):
                    decode_video_id(value)

    def test_common_image_sizes_map_to_aspect_ratios(self):
        self.assertEqual(image_size_to_ratio("1024x1024"), "1:1")
        self.assertEqual(image_size_to_ratio("1536x1024"), "3:2")
        self.assertEqual(image_size_to_ratio("1024x1536"), "2:3")
        self.assertEqual(image_size_to_ratio("1024x1824"), "9:16")
        self.assertEqual(image_size_to_ratio("1824x1024"), "16:9")
        self.assertEqual(image_size_to_ratio("1024x1365"), "3:4")
        with self.assertRaises(OpenAICompatError) as caught:
            image_size_to_ratio("1024x2048")
        self.assertEqual(caught.exception.param, "size")

    def test_common_video_sizes_map_to_aspect_ratios(self):
        self.assertEqual(video_size_to_ratio("1280x720"), "16:9")
        self.assertEqual(video_size_to_ratio("720x1280"), "9:16")
        self.assertEqual(video_size_to_ratio("1024x1024"), "1:1")
        with self.assertRaises(OpenAICompatError):
            video_size_to_ratio("640x360")

    def test_model_aliases_resolve_to_provider_defaults_or_overrides(self):
        config = {
            "oreate": {
                "default_image_model": "Provider Image",
                "default_video_model": "Provider Video",
            },
            "openai_compat": {
                "image_model_aliases": {"custom-image": "Provider Image 2"},
                "video_model_aliases": {"custom-video": "Provider Video 2"},
            },
        }
        self.assertEqual(resolve_openai_model("image", "gpt-image-1", config), "Provider Image")
        self.assertEqual(resolve_openai_model("video", "sora-2", config), "Provider Video")
        self.assertEqual(resolve_openai_model("image", "custom-image", config), "Provider Image 2")
        self.assertEqual(resolve_openai_model("video", "custom-video", config), "Provider Video 2")
        self.assertEqual(resolve_openai_model("video", "Native Provider Name", config), "Native Provider Name")

    def test_openai_error_payload_has_stable_shape(self):
        payload = openai_error_payload(
            "size is unsupported",
            error_type="invalid_request_error",
            param="size",
            code="invalid_size",
        )
        self.assertEqual(
            payload,
            {
                "error": {
                    "message": "size is unsupported",
                    "type": "invalid_request_error",
                    "param": "size",
                    "code": "invalid_size",
                }
            },
        )

    def test_task_maps_to_video_object_without_internal_provider_data(self):
        task = {
            "id": 9,
            "status": "hydrating",
            "created_at": 1710000000.9,
            "model_name": "Provider Video",
            "duration": 5,
            "resolution": "720",
            "ratio": "16:9",
            "account_id": 123,
            "response": {"raw": "must not leak"},
            "assets": [],
        }
        result = task_to_video_object(task, requested_model="sora-2", requested_size="1280x720")
        self.assertEqual(result["id"], "video_9")
        self.assertEqual(result["object"], "video")
        self.assertEqual(result["status"], "in_progress")
        self.assertEqual(result["progress"], 75)
        self.assertEqual(result["model"], "sora-2")
        self.assertEqual(result["seconds"], "5")
        self.assertEqual(result["size"], "1280x720")
        self.assertNotIn("account_id", result)
        self.assertNotIn("response", result)


TEST_ENCRYPTION_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


class FakeChatResponse:
    def __init__(self, *, status_code=200, payload=None, chunks=(), headers=None, json_error=None):
        self.status_code = status_code
        self.payload = payload
        self.chunks = list(chunks)
        self.headers = headers or {}
        self.json_error = json_error
        self.closed = False
        self.iterated = False

    def json(self):
        if self.json_error is not None:
            raise self.json_error
        return self.payload

    def iter_content(self, chunk_size=8192):
        self.iterated = True
        yield from self.chunks

    def close(self):
        self.closed = True


class OpenAICompatEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "accounts.db"
        self.config_path = Path(self.tmp.name) / "config.json"
        self.db_patch = patch.object(server, "DB_PATH", self.db_path)
        self.config_patch = patch.object(server, "CONFIG_PATH", self.config_path)
        self.db_patch.start()
        self.config_patch.start()
        base_cfg = json.loads(json.dumps(server.CFG))
        self.cfg_patch = patch.object(
            server,
            "CFG",
            server.deep_merge(
                base_cfg,
                {
                    "server": {
                        "admin_username": "admin",
                        "admin_password": "test-admin-password",
                        "encryption_key": TEST_ENCRYPTION_KEY,
                    },
                    "oreate": {
                        "default_image_model": "Provider Image",
                        "default_image_resolution": "1K",
                        "default_image_ratio": "1:1",
                        "default_video_model": "Provider Video",
                        "default_video_resolution": "720",
                        "default_video_ratio": "16:9",
                        "default_video_duration": 5,
                    },
                    "gateway": {"enable_background_worker": False},
                },
            ),
        )
        self.cfg_patch.start()
        server.RATE_BUCKETS.clear()
        server.init_db()
        self.client = TestClient(server.app)
        self.seed_account_and_scoped_key()

    def tearDown(self):
        server.RATE_BUCKETS.clear()
        self.cfg_patch.stop()
        self.config_patch.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    @staticmethod
    def image_capabilities():
        return {
            "data": {
                "factory": [
                    {
                        "models": [
                            {
                                "modelName": "Provider Image",
                                "modelDesc": "Image model",
                                "resolution": ["1K", "2K"],
                                "size": [
                                    {"ratio": "1:1"},
                                    {"ratio": "16:9"},
                                    {"ratio": "9:16"},
                                ],
                                "pointCost": [{"resolution": "1K", "point": 5}],
                            },
                            {
                                "modelName": "Hidden Image",
                                "modelDesc": "Not visible to this key",
                                "resolution": ["1K"],
                                "size": [{"ratio": "1:1"}],
                                "pointCost": [{"resolution": "1K", "point": 5}],
                            },
                        ]
                    }
                ]
            }
        }

    @staticmethod
    def video_capabilities():
        return {
            "models": {
                "data": {
                    "models": [
                        {
                            "modelName": "Provider Video",
                            "duration": [5],
                            "videoResolution": ["720"],
                            "videoSize": [{"ratio": "16:9"}],
                            "pointCostImage": [{"duration": 5, "resolution": "720", "point": 10}],
                        }
                    ]
                }
            },
            "scenes": {
                "data": {
                    "scenes": [
                        {
                            "sceneId": "text_or_image",
                            "sceneName": {"en": "Text or image"},
                            "description": {"en": "Basic video"},
                        },
                        {
                            "sceneId": "reference",
                            "sceneName": {"en": "Reference"},
                            "description": {"en": "Reference video"},
                        }
                    ]
                }
            },
        }

    def seed_account_and_scoped_key(self):
        now = time.time()
        conn = server.db_conn()
        conn.execute(
            """
            INSERT INTO accounts(
                email,password,status,source,ouid,ouss,model_info_json,video_info_json,
                rest_point,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "openai@example.com",
                server.encrypt_secret_value("password"),
                "verified",
                "manual",
                server.encrypt_secret_value("ouid"),
                server.encrypt_secret_value("ouss"),
                json.dumps(self.image_capabilities()),
                json.dumps(self.video_capabilities()),
                100,
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO api_keys(
                key,name,enabled,created_at,allowed_kinds,allowed_models,
                allowed_resolutions,allow_uploads,allow_experimental
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                "openai-model-key",
                "OpenAI model key",
                1,
                now,
                json.dumps(["image"]),
                json.dumps(["Provider Image"]),
                json.dumps(["1K"]),
                0,
                0,
            ),
        )
        conn.execute(
            """
            INSERT INTO api_keys(
                key,name,enabled,created_at,allowed_kinds,allowed_models,
                allowed_resolutions,allow_uploads,allow_experimental
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                "openai-image-upload-key",
                "OpenAI image upload key",
                1,
                now,
                json.dumps(["image"]),
                json.dumps(["Provider Image"]),
                json.dumps(["1K"]),
                1,
                0,
            ),
        )
        conn.execute(
            """
            INSERT INTO api_keys(
                key,name,enabled,created_at,allowed_kinds,allowed_models,allowed_scenes,
                allowed_resolutions,allowed_durations,allow_uploads,allow_experimental
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "openai-video-key",
                "OpenAI video key",
                1,
                now,
                json.dumps(["video"]),
                json.dumps(["Provider Video"]),
                json.dumps(["text_or_image"]),
                json.dumps(["720"]),
                json.dumps([5]),
                0,
                0,
            ),
        )
        conn.execute(
            """
            INSERT INTO api_keys(
                key,name,enabled,created_at,allowed_kinds,allowed_models,allowed_scenes,
                allowed_resolutions,allowed_durations,allow_uploads,allow_experimental
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "openai-video-upload-key",
                "OpenAI video upload key",
                1,
                now,
                json.dumps(["video"]),
                json.dumps(["Provider Video"]),
                json.dumps(["text_or_image", "reference"]),
                json.dumps(["720"]),
                json.dumps([5]),
                1,
                1,
            ),
        )
        conn.execute(
            """
            INSERT INTO api_keys(
                key,name,enabled,created_at,allowed_kinds,allowed_models,
                allow_uploads,allow_experimental
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                "openai-chat-key",
                "OpenAI chat key",
                1,
                now,
                json.dumps(["chat"]),
                json.dumps(["configured-chat-model"]),
                0,
                0,
            ),
        )
        conn.commit()
        conn.close()

    def api_key_id(self, key: str) -> int:
        conn = server.db_conn()
        row = conn.execute("SELECT id FROM api_keys WHERE key=?", (key,)).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        return int(row["id"])

    @staticmethod
    def synthetic_watermarked_image_bytes() -> bytes:
        image = Image.new("RGB", (360, 640), (74, 52, 96))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 359, 560), fill=(105, 78, 130))
        font = ImageFont.load_default(size=24)
        draw.text((232, 588), "Oreate AI", fill=(245, 245, 245), font=font)
        payload = BytesIO()
        image.save(payload, format="JPEG", quality=95)
        return payload.getvalue()

    def seed_video_task(self, *, status: str = "queued", assets=None, api_key: str = "openai-video-key") -> int:
        return server.save_task(
            1,
            "video",
            "video prompt",
            {
                "kind": "video",
                "prompt": "video prompt",
                "model_name": "Provider Video",
                "ratio": "16:9",
                "resolution": "720",
                "duration": 5,
                "scene_id": "text_or_image",
            },
            {"status": status, "assets": assets or []},
            status=status,
            api_key_id=self.api_key_id(api_key),
            model_name="Provider Video",
            ratio="16:9",
            resolution="720",
            duration=5,
            scene_id="text_or_image",
        )

    def test_models_endpoint_uses_openai_error_envelope_for_authentication(self):
        for path in ("/v1/models", "/v1/models/gpt-image-1"):
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 401)
                self.assertEqual(set(response.json()), {"error"})
                self.assertEqual(response.json()["error"]["type"], "authentication_error")

    def test_compatibility_routes_use_openai_envelope_for_request_validation(self):
        for path in ("/v1/images/generations", "/v1/videos"):
            with self.subTest(path=path):
                response = self.client.post(
                    path,
                    headers={"Authorization": "Bearer openai-model-key"},
                    json={"model": "gpt-image-1"},
                )

                self.assertEqual(response.status_code, 422)
                self.assertEqual(set(response.json()), {"error"})
                self.assertEqual(response.json()["error"]["type"], "invalid_request_error")
                self.assertEqual(response.json()["error"]["param"], "prompt")
                self.assertEqual(response.json()["error"]["code"], "validation_error")

    def configure_chat_provider(self):
        server.CFG["chat"] = {
            "provider": "openai",
            "base_url": "https://chat.example.test/v1",
            "api_key": "upstream-secret",
            "model": "configured-chat-model",
        }

    def test_chat_completions_uses_openai_error_contract_for_auth_and_validation(self):
        unauthenticated = self.client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(set(unauthenticated.json()), {"error"})
        self.assertEqual(unauthenticated.json()["error"]["type"], "authentication_error")

        invalid = self.client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer openai-chat-key"},
            json={"messages": []},
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(set(invalid.json()), {"error"})
        self.assertEqual(invalid.json()["error"]["param"], "messages")
        self.assertEqual(invalid.json()["error"]["code"], "validation_error")

    def test_chat_completions_forwards_extra_fields_and_closes_non_stream_response(self):
        self.configure_chat_provider()
        upstream_payload = {
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "hello"}}],
        }
        upstream = FakeChatResponse(status_code=200, payload=upstream_payload)

        with patch.object(server.requests, "post", return_value=upstream) as post:
            response = self.client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer openai-chat-key"},
                json={
                    "model": "oreate-chat",
                    "messages": [{"role": "user", "content": "hello"}],
                    "top_p": 0.7,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), upstream_payload)
        self.assertTrue(upstream.closed)
        request = post.call_args
        self.assertEqual(request.args[0], "https://chat.example.test/v1/chat/completions")
        self.assertEqual(request.kwargs["json"]["model"], "configured-chat-model")
        self.assertEqual(request.kwargs["json"]["top_p"], 0.7)
        self.assertFalse(request.kwargs["stream"])
        self.assertEqual(request.kwargs["headers"]["Authorization"], "Bearer upstream-secret")
        self.assertTrue(response.headers["x-request-id"].startswith("req_"))

        conn = server.db_conn()
        usage = conn.execute(
            "SELECT * FROM usage_log WHERE api_key_id=? AND kind='chat'",
            (self.api_key_id("openai-chat-key"),),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(usage)
        self.assertEqual(usage["account_id"], 0)
        self.assertEqual(usage["prompt"], "1 chat message")
        self.assertNotIn("hello", usage["prompt"])
        self.assertEqual(usage["model_name"], "configured-chat-model")
        self.assertEqual(usage["estimated_point_cost"], 0)
        self.assertEqual(usage["status"], "completed")
        self.assertEqual(usage["status_code"], 200)

    def test_chat_completions_enforces_api_key_scope_and_single_configured_model(self):
        self.configure_chat_provider()
        body = {"messages": [{"role": "user", "content": "hello"}]}

        with patch.object(server.requests, "post") as post:
            forbidden = self.client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer openai-model-key"},
                json=body,
            )
            conn = server.db_conn()
            conn.execute("UPDATE api_keys SET allowed_kinds=NULL WHERE key=?", ("openai-chat-key",))
            conn.commit()
            conn.close()
            legacy_unscoped = self.client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer openai-chat-key"},
                json=body,
            )
            conn = server.db_conn()
            conn.execute(
                "UPDATE api_keys SET allowed_kinds=? WHERE key=?",
                (json.dumps(["chat"]), "openai-chat-key"),
            )
            conn.commit()
            conn.close()
            unknown_model = self.client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer openai-chat-key"},
                json={**body, "model": "unconfigured-expensive-model"},
            )

        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(forbidden.json()["error"]["code"], "api_key_kind_forbidden")
        self.assertEqual(legacy_unscoped.status_code, 403)
        self.assertEqual(legacy_unscoped.json()["error"]["code"], "api_key_kind_forbidden")
        self.assertEqual(unknown_model.status_code, 404)
        self.assertEqual(unknown_model.json()["error"]["code"], "model_not_found")
        self.assertEqual(unknown_model.json()["error"]["param"], "model")
        post.assert_not_called()

    def test_chat_completions_enforces_rate_limit_before_upstream_call(self):
        self.configure_chat_provider()
        conn = server.db_conn()
        conn.execute(
            "UPDATE api_keys SET rate_limit_per_minute=1,daily_request_limit=0 WHERE key=?",
            ("openai-chat-key",),
        )
        conn.commit()
        conn.close()
        upstream = FakeChatResponse(status_code=200, payload={"choices": []})
        headers = {"Authorization": "Bearer openai-chat-key"}
        body = {"messages": [{"role": "user", "content": "hello"}]}

        with patch.object(server.requests, "post", return_value=upstream) as post:
            accepted = self.client.post("/v1/chat/completions", headers=headers, json=body)
            limited = self.client.post("/v1/chat/completions", headers=headers, json=body)

        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.json()["error"]["code"], "rate_limited")
        self.assertEqual(post.call_count, 1)

    def test_chat_completions_enforces_daily_request_limit_before_upstream_call(self):
        self.configure_chat_provider()
        conn = server.db_conn()
        conn.execute(
            "UPDATE api_keys SET rate_limit_per_minute=0,daily_request_limit=1 WHERE key=?",
            ("openai-chat-key",),
        )
        conn.commit()
        conn.close()
        upstream = FakeChatResponse(status_code=200, payload={"choices": []})
        headers = {"Authorization": "Bearer openai-chat-key"}
        body = {"messages": [{"role": "user", "content": "hello"}]}

        with patch.object(server.requests, "post", return_value=upstream) as post:
            accepted = self.client.post("/v1/chat/completions", headers=headers, json=body)
            limited = self.client.post("/v1/chat/completions", headers=headers, json=body)

        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.json()["error"]["code"], "daily_request_limit_exceeded")
        self.assertEqual(post.call_count, 1)

    def test_chat_completions_rejects_upstream_error_before_streaming(self):
        self.configure_chat_provider()
        upstream = FakeChatResponse(
            status_code=429,
            payload={"error": {"message": "provider rate limited"}},
            chunks=[b"should-not-stream"],
        )

        with patch.object(server.requests, "post", return_value=upstream):
            response = self.client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer openai-chat-key"},
                json={
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": True,
                },
            )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"]["type"], "rate_limit_error")
        self.assertEqual(response.json()["error"]["code"], "upstream_error")
        self.assertEqual(response.json()["error"]["message"], "provider rate limited")
        self.assertFalse(upstream.iterated)
        self.assertTrue(upstream.closed)

    def test_chat_completions_streams_raw_chunks_and_closes_response(self):
        self.configure_chat_provider()
        upstream = FakeChatResponse(
            status_code=200,
            chunks=[b'data: {"delta":"one"}\n\n', b"data: [DONE]\n\n"],
            headers={"Content-Type": "text/event-stream"},
        )

        with patch.object(server.requests, "post", return_value=upstream):
            response = self.client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer openai-chat-key"},
                json={
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.content,
            b'data: {"delta":"one"}\n\ndata: [DONE]\n\n',
        )
        self.assertTrue(response.headers["x-request-id"].startswith("req_"))
        self.assertTrue(upstream.iterated)
        self.assertTrue(upstream.closed)

    def test_chat_completions_maps_transport_and_invalid_json_failures(self):
        self.configure_chat_provider()
        request_body = {"messages": [{"role": "user", "content": "hello"}]}
        headers = {"Authorization": "Bearer openai-chat-key"}

        with patch.object(
            server.requests,
            "post",
            side_effect=server.requests.Timeout("private upstream detail"),
        ):
            unavailable = self.client.post(
                "/v1/chat/completions",
                headers=headers,
                json=request_body,
            )
        self.assertEqual(unavailable.status_code, 502)
        self.assertEqual(unavailable.json()["error"]["code"], "upstream_unavailable")
        self.assertNotIn("private upstream detail", unavailable.text)

        invalid_upstream = FakeChatResponse(
            status_code=200,
            json_error=ValueError("invalid JSON"),
        )
        with patch.object(server.requests, "post", return_value=invalid_upstream):
            invalid = self.client.post(
                "/v1/chat/completions",
                headers=headers,
                json=request_body,
            )
        self.assertEqual(invalid.status_code, 502)
        self.assertEqual(invalid.json()["error"]["code"], "invalid_upstream_response")
        self.assertTrue(invalid_upstream.closed)

    def test_models_endpoint_lists_chat_only_for_explicit_chat_scope(self):
        self.configure_chat_provider()

        chat_response = self.client.get(
            "/v1/models",
            headers={"Authorization": "Bearer openai-chat-key"},
        )
        image_response = self.client.get(
            "/v1/models",
            headers={"Authorization": "Bearer openai-model-key"},
        )
        detail_response = self.client.get(
            "/v1/models/oreate-chat",
            headers={"Authorization": "Bearer openai-chat-key"},
        )

        self.assertEqual(chat_response.status_code, 200)
        self.assertEqual(
            {item["id"] for item in chat_response.json()["data"]},
            {"oreate-chat", "configured-chat-model"},
        )
        self.assertNotIn(
            "oreate-chat",
            {item["id"] for item in image_response.json()["data"]},
        )
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.json()["id"], "oreate-chat")

    def test_models_endpoint_lists_only_models_visible_to_api_key(self):
        response = self.client.get(
            "/v1/models",
            headers={"Authorization": "Bearer openai-model-key"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["object"], "list")
        model_ids = {item["id"] for item in payload["data"]}
        self.assertIn("gpt-image-1", model_ids)
        self.assertIn("Provider Image", model_ids)
        self.assertNotIn("Hidden Image", model_ids)
        self.assertNotIn("sora-2", model_ids)
        for item in payload["data"]:
            self.assertEqual(set(item), {"id", "object", "created", "owned_by"})

    def test_models_endpoint_retrieves_visible_model_and_hides_inaccessible_model(self):
        visible = self.client.get(
            "/v1/models/gpt-image-1",
            headers={"Authorization": "Bearer openai-model-key"},
        )

        self.assertEqual(visible.status_code, 200)
        self.assertEqual(
            visible.json(),
            {
                "id": "gpt-image-1",
                "object": "model",
                "created": 0,
                "owned_by": "oreateai-gateway",
            },
        )

        hidden = self.client.get(
            "/v1/models/sora-2",
            headers={"Authorization": "Bearer openai-model-key"},
        )

        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(hidden.json()["error"]["param"], "model")
        self.assertEqual(hidden.json()["error"]["code"], "model_not_found")

    def test_canvas_origin_can_preflight_and_fetch_models(self):
        preflight = self.client.options(
            "/v1/models",
            headers={
                "Origin": "https://canvas.best",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )

        self.assertEqual(preflight.status_code, 200)
        self.assertEqual(preflight.headers["access-control-allow-origin"], "https://canvas.best")
        self.assertIn("GET", preflight.headers["access-control-allow-methods"])
        self.assertIn("authorization", preflight.headers["access-control-allow-headers"].lower())

        response = self.client.get(
            "/v1/models",
            headers={
                "Authorization": "Bearer openai-model-key",
                "Origin": "https://canvas.best",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], "https://canvas.best")
        self.assertTrue(response.json()["data"])

    def test_unconfigured_origin_does_not_receive_cors_access(self):
        response = self.client.get(
            "/v1/models",
            headers={
                "Authorization": "Bearer openai-model-key",
                "Origin": "https://untrusted.example",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_native_capabilities_are_filtered_by_same_api_key_scope(self):
        response = self.client.get(
            "/v1/capabilities",
            headers={"Authorization": "Bearer openai-model-key"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["name"] for item in response.json()["image"]["models"]], ["Provider Image"])
        self.assertEqual(response.json()["image"]["models"][0]["resolutions"], ["1K"])
        self.assertEqual(response.json()["video"]["models"], [])
        self.assertEqual(response.json()["video"]["scenes"], [])

    def test_image_generation_returns_openai_url_response_and_maps_options(self):
        source_image = self.synthetic_watermarked_image_bytes()
        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "create_chat_session", return_value={"chatId": "chat-image", "focusId": "focus-image"}),
            patch.object(
                server.CLIENT,
                "stream_generation",
                return_value={"events": [{"event": "end"}], "error": None, "status": "streamed"},
            ),
            patch.object(
                server.CLIENT,
                "hydrate_generation_result",
                return_value={"raw": {}, "assets": ["https://cdn.oreateai.com/aiimage/result.png"]},
            ),
        ):
            response = self.client.post(
                "/v1/images/generations",
                headers={"Authorization": "Bearer openai-model-key"},
                json={
                    "model": "gpt-image-1",
                    "prompt": "a production gateway",
                    "size": "1024x1024",
                    "n": 1,
                    "response_format": "url",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"created", "data"})
        data = response.json()["data"]
        self.assertEqual(data[0]["revised_prompt"], "a production gateway")
        clean_url = data[0]["url"]
        parsed_clean_url = urlparse(clean_url)
        self.assertEqual(parsed_clean_url.scheme, "http")
        self.assertEqual(parsed_clean_url.netloc, "testserver")
        self.assertRegex(parsed_clean_url.path, r"^/v1/tasks/\d+/assets/0/clean$")
        self.assertEqual(len(parse_qs(parsed_clean_url.query).get("signature", [])), 1)
        conn = server.db_conn()
        task = conn.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        self.assertEqual(task["model_name"], "Provider Image")
        self.assertEqual(task["ratio"], "1:1")
        self.assertEqual(task["resolution"], "1K")
        self.assertEqual(task["status"], "completed")
        self.assertEqual(
            json.loads(task["assets_json"]),
            ["https://cdn.oreateai.com/aiimage/result.png"],
        )

        with patch.object(server, "fetch_remote_image_asset", return_value=source_image):
            cleaned = self.client.get(
                clean_url,
                headers={"Origin": "https://canvas.best"},
            )
        self.assertEqual(cleaned.status_code, 200)
        self.assertEqual(cleaned.headers["access-control-allow-origin"], "https://canvas.best")
        self.assertEqual(cleaned.headers["x-watermark-removed"], "true")
        self.assertIn("public", cleaned.headers["cache-control"])
        self.assertNotEqual(cleaned.content, source_image)
        with Image.open(BytesIO(cleaned.content)) as result:
            self.assertEqual(result.size, (360, 640))

        tampered_query = parse_qs(parsed_clean_url.query)
        tampered_query["signature"] = ["0" * 64]
        tampered = self.client.get(
            f"{parsed_clean_url.path}?signature={tampered_query['signature'][0]}"
        )
        self.assertEqual(tampered.status_code, 404)

    def test_image_generation_returns_base64_for_canvas_openai_requests(self):
        source_image = self.synthetic_watermarked_image_bytes()
        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "create_chat_session", return_value={"chatId": "chat-image", "focusId": "focus-image"}),
            patch.object(
                server.CLIENT,
                "stream_generation",
                return_value={"events": [{"event": "end"}], "error": None, "status": "streamed"},
            ),
            patch.object(
                server.CLIENT,
                "hydrate_generation_result",
                return_value={"raw": {}, "assets": ["https://cdn.oreateai.com/aiimage/result.png"]},
            ),
            patch.object(server, "fetch_remote_image_asset", return_value=source_image) as fetch_asset,
        ):
            response = self.client.post(
                "/v1/images/generations",
                headers={
                    "Authorization": "Bearer openai-model-key",
                    "Origin": "https://canvas.best",
                },
                json={
                    "model": "gpt-image-1",
                    "prompt": "a canvas integration",
                    "size": "1024x1824",
                    "n": 1,
                    "response_format": "b64_json",
                    "output_format": "png",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], "https://canvas.best")
        result_data = response.json()["data"][0]
        self.assertEqual(result_data["revised_prompt"], "a canvas integration")
        cleaned_image = base64.b64decode(result_data["b64_json"])
        self.assertNotEqual(cleaned_image, source_image)
        with Image.open(BytesIO(cleaned_image)) as result:
            self.assertEqual(result.size, (360, 640))
            bottom_right = result.crop((250, 570, 360, 640))
            extrema = bottom_right.convert("L").getextrema()
            self.assertLess(extrema[1] - extrema[0], 35)
        fetch_asset.assert_called_once_with("https://cdn.oreateai.com/aiimage/result.png")
        conn = server.db_conn()
        task = conn.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        self.assertEqual(task["ratio"], "9:16")

    def test_image_generation_rejects_unsupported_count_without_creating_task(self):
        response = self.client.post(
            "/v1/images/generations",
            headers={"Authorization": "Bearer openai-model-key"},
            json={"model": "gpt-image-1", "prompt": "two images", "n": 2},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["param"], "n")
        self.assertEqual(response.json()["error"]["code"], "unsupported_n")
        conn = server.db_conn()
        task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        conn.close()
        self.assertEqual(task_count, 0)

    def test_image_generation_rejects_streaming_without_creating_task(self):
        response = self.client.post(
            "/v1/images/generations",
            headers={"Authorization": "Bearer openai-model-key"},
            json={
                "model": "gpt-image-1",
                "prompt": "stream this image",
                "stream": True,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["param"], "stream")
        self.assertEqual(response.json()["error"]["code"], "unsupported_stream")
        conn = server.db_conn()
        task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        conn.close()
        self.assertEqual(task_count, 0)

    def test_image_edits_accepts_sub2api_multipart_request_and_uses_uploaded_reference(self):
        uploaded = {
            "object": "uploads/reference.png",
            "fileName": "reference.png",
            "fileExt": "png",
            "contentType": "image/png",
            "originSize": 7,
        }
        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "upload_file_bytes", return_value=uploaded) as upload_file,
            patch.object(server.CLIENT, "create_chat_session", return_value={"chatId": "chat-edit", "focusId": "focus-edit"}),
            patch.object(
                server.CLIENT,
                "stream_generation",
                return_value={"events": [{"event": "end"}], "error": None, "status": "streamed"},
            ),
            patch.object(
                server.CLIENT,
                "hydrate_generation_result",
                return_value={"raw": {}, "assets": ["https://cdn.oreateai.com/aiimage/edited.png"]},
            ),
        ):
            response = self.client.post(
                "/v1/images/edits",
                headers={"Authorization": "Bearer openai-image-upload-key"},
                data={
                    "model": "gpt-image-1",
                    "prompt": "replace the background",
                    "size": "1024x1024",
                    "n": "1",
                    "response_format": "url",
                },
                files={"image": ("reference.png", b"pngdata", "image/png")},
            )

        self.assertEqual(response.status_code, 200)
        result = response.json()["data"][0]
        self.assertEqual(result["revised_prompt"], "replace the background")
        self.assertRegex(
            result["url"],
            r"^http://testserver/v1/tasks/\d+/assets/0/clean\?signature=[0-9a-f]{64}$",
        )
        upload_file.assert_called_once()
        conn = server.db_conn()
        task = conn.execute("SELECT account_id,payload_json FROM tasks ORDER BY id DESC LIMIT 1").fetchone()
        upload = conn.execute(
            "SELECT object_path,attachment_json,status FROM uploaded_media ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        payload = json.loads(task["payload_json"])
        self.assertEqual(payload["reference_images"], [uploaded])
        self.assertEqual(task["account_id"], 1)
        self.assertEqual(upload["object_path"], "uploads/reference.png")
        self.assertEqual(server.upload_media_kind(json.loads(upload["attachment_json"])), "image")
        self.assertEqual(upload["status"], "completed")

    def test_image_edits_rejects_masks_before_upload_or_task_creation(self):
        with patch.object(server.CLIENT, "upload_file_bytes") as upload_file:
            response = self.client.post(
                "/v1/images/edits",
                headers={"Authorization": "Bearer openai-image-upload-key"},
                data={"model": "gpt-image-1", "prompt": "masked edit"},
                files={
                    "image": ("reference.png", b"pngdata", "image/png"),
                    "mask": ("mask.png", b"maskdata", "image/png"),
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["param"], "mask")
        self.assertEqual(response.json()["error"]["code"], "unsupported_mask")
        upload_file.assert_not_called()
        conn = server.db_conn()
        task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        upload_count = conn.execute("SELECT COUNT(*) FROM uploaded_media").fetchone()[0]
        conn.close()
        self.assertEqual(task_count, 0)
        self.assertEqual(upload_count, 0)

    def test_admin_docs_explain_new_api_and_sub2api_base_url_rules(self):
        html = server.ADMIN_HTML

        self.assertIn("new-api 接入", html)
        self.assertIn("sub2api 接入", html)
        self.assertIn('id="api-doc-new-api-base"', html)
        self.assertIn('id="api-doc-sub2api-base"', html)
        self.assertIn("图片生成", html)
        self.assertIn("不支持 stream=true", html)

    def test_image_generation_timeout_returns_openai_error_and_keeps_single_task(self):
        original = server.CFG["gateway"].get("enable_background_worker")
        original_timeout = server.CFG.get("openai_compat", {}).get("image_sync_timeout_seconds")
        server.CFG["gateway"]["enable_background_worker"] = True
        server.CFG.setdefault("openai_compat", {})["image_sync_timeout_seconds"] = 0.01
        try:
            response = self.client.post(
                "/v1/images/generations",
                headers={"Authorization": "Bearer openai-model-key"},
                json={"model": "gpt-image-1", "prompt": "slow image", "n": 1},
            )
        finally:
            server.CFG["gateway"]["enable_background_worker"] = original
            if original_timeout is None:
                server.CFG.get("openai_compat", {}).pop("image_sync_timeout_seconds", None)
            else:
                server.CFG.setdefault("openai_compat", {})["image_sync_timeout_seconds"] = original_timeout

        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.json()["error"]["code"], "image_generation_timeout")
        conn = server.db_conn()
        tasks = conn.execute("SELECT id,status FROM tasks").fetchall()
        conn.close()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["status"], "queued")

    def test_video_create_and_generation_alias_return_openai_job_objects(self):
        for path in ("/v1/videos", "/v1/videos/generations"):
            with self.subTest(path=path):
                response = self.client.post(
                    path,
                    headers={"Authorization": "Bearer openai-video-key"},
                    json={
                        "model": "sora-2",
                        "prompt": "a gateway status animation",
                        "seconds": "5",
                        "size": "1280x720",
                    },
                )

                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["object"], "video")
                self.assertTrue(payload["id"].startswith("video_"))
                self.assertEqual(payload["status"], "queued")
                self.assertEqual(payload["model"], "sora-2")
                self.assertEqual(payload["seconds"], "5")
                self.assertEqual(payload["size"], "1280x720")
                self.assertNotIn("account_id", payload)
                self.assertNotIn("response", payload)

        conn = server.db_conn()
        tasks = conn.execute("SELECT model_name,ratio,resolution,duration,status FROM tasks ORDER BY id").fetchall()
        conn.close()
        self.assertEqual(len(tasks), 2)
        for task in tasks:
            self.assertEqual(task["model_name"], "Provider Video")
            self.assertEqual(task["ratio"], "16:9")
            self.assertEqual(task["resolution"], "720")
            self.assertEqual(task["duration"], 5)
            self.assertEqual(task["status"], "queued")

    def test_video_generation_accepts_json_input_reference(self):
        original_cfg = server.CFG
        server.CFG = server.deep_merge(
            original_cfg,
            {
                "gateway": {
                    "scene_policies": {
                        "reference": {
                            "enabled": True,
                            "experimental": False,
                            "verification_status": "live_verified",
                            "risk_level": "low",
                        }
                    }
                }
            },
        )
        image_attachment = {
            "fileName": "ref-image",
            "fileExt": "png",
            "originSize": 12,
            "object": "uploads/ref-image.png",
            "status": "completed",
        }
        video_attachment = {
            "fileName": "ref-video",
            "fileExt": "mp4",
            "originSize": 34,
            "object": "uploads/ref-video.mp4",
            "status": "completed",
            "videoDurationSec": 4,
        }
        server.save_uploaded_media_record(self.api_key_id("openai-video-upload-key"), 1, image_attachment)
        server.save_uploaded_media_record(self.api_key_id("openai-video-upload-key"), 1, video_attachment)
        try:
            response = self.client.post(
                "/v1/videos",
                headers={"Authorization": "Bearer openai-video-upload-key"},
                json={
                    "model": "sora-2",
                    "prompt": "use uploaded references",
                    "seconds": "5",
                    "size": "1280x720",
                    "input_reference": [
                        {"object": "uploads/ref-image.png"},
                        {"object": "uploads/ref-video.mp4"},
                    ],
                },
            )
        finally:
            server.CFG = original_cfg

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["object"], "video")
        conn = server.db_conn()
        task = conn.execute("SELECT payload_json,scene_id,account_id FROM tasks ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        saved = json.loads(task["payload_json"])
        self.assertEqual(task["scene_id"], "reference")
        self.assertEqual(task["account_id"], 1)
        self.assertEqual(saved["scene_id"], "reference")
        self.assertEqual(saved["reference_images"][0]["object"], "uploads/ref-image.png")
        self.assertEqual(saved["reference_videos"][0]["object"], "uploads/ref-video.mp4")

    def test_video_generation_accepts_multipart_input_reference_files(self):
        original_cfg = server.CFG
        server.CFG = server.deep_merge(
            original_cfg,
            {
                "gateway": {
                    "scene_policies": {
                        "reference": {
                            "enabled": True,
                            "experimental": False,
                            "verification_status": "live_verified",
                            "risk_level": "low",
                        }
                    }
                }
            },
        )
        uploaded = [
            {
                "fileName": "ref-image",
                "fileExt": "png",
                "originSize": 4,
                "object": "uploads/ref-image.png",
                "status": "completed",
            },
            {
                "fileName": "ref-video",
                "fileExt": "mp4",
                "originSize": 8,
                "object": "uploads/ref-video.mp4",
                "status": "completed",
                "videoDurationSec": 4,
            },
        ]
        try:
            with (
                patch.object(server.CLIENT, "session_from_account", return_value=object()),
                patch.object(server.CLIENT, "upload_file_bytes", side_effect=uploaded) as upload_file,
            ):
                response = self.client.post(
                    "/v1/videos",
                    headers={"Authorization": "Bearer openai-video-upload-key"},
                    data={
                        "model": "sora-2",
                        "prompt": "multipart references",
                        "seconds": "5",
                        "size": "1280x720",
                    },
                    files=[
                        ("input_reference", ("ref-image.png", b"pngdata", "image/png")),
                        ("input_reference", ("ref-video.mp4", b"mp4data", "video/mp4")),
                    ],
                )
        finally:
            server.CFG = original_cfg

        self.assertEqual(response.status_code, 200)
        upload_file.assert_called()
        self.assertEqual(upload_file.call_count, 2)
        conn = server.db_conn()
        task = conn.execute("SELECT payload_json,scene_id,account_id FROM tasks ORDER BY id DESC LIMIT 1").fetchone()
        uploaded_rows = conn.execute(
            "SELECT object_path,account_id,status FROM uploaded_media WHERE api_key_id=? ORDER BY object_path",
            (self.api_key_id("openai-video-upload-key"),),
        ).fetchall()
        conn.close()
        saved = json.loads(task["payload_json"])
        self.assertEqual(task["scene_id"], "reference")
        self.assertEqual(task["account_id"], 1)
        self.assertEqual(saved["scene_id"], "reference")
        self.assertEqual(saved["reference_images"][0]["object"], "uploads/ref-image.png")
        self.assertEqual(saved["reference_videos"][0]["object"], "uploads/ref-video.mp4")
        self.assertEqual(
            [(row["object_path"], row["account_id"], row["status"]) for row in uploaded_rows],
            [
                ("uploads/ref-image.png", 1, "completed"),
                ("uploads/ref-video.mp4", 1, "completed"),
            ],
        )

    def test_video_generation_accepts_canvas_bracketed_input_reference_files(self):
        original_cfg = server.CFG
        server.CFG = server.deep_merge(
            original_cfg,
            {
                "gateway": {
                    "scene_policies": {
                        "reference": {
                            "enabled": True,
                            "experimental": False,
                            "verification_status": "live_verified",
                            "risk_level": "low",
                        }
                    }
                }
            },
        )
        uploaded = {
            "fileName": "canvas-reference",
            "fileExt": "png",
            "originSize": 7,
            "object": "uploads/canvas-reference.png",
            "status": "completed",
        }
        try:
            with (
                patch.object(server.CLIENT, "session_from_account", return_value=object()),
                patch.object(server.CLIENT, "upload_file_bytes", return_value=uploaded) as upload_file,
            ):
                response = self.client.post(
                    "/v1/videos",
                    headers={"Authorization": "Bearer openai-video-upload-key"},
                    data={
                        "model": "sora-2",
                        "prompt": "canvas reference",
                        "seconds": "5",
                        "size": "1280x720",
                    },
                    files=[("input_reference[]", ("canvas-reference.png", b"pngdata", "image/png"))],
                )
        finally:
            server.CFG = original_cfg

        self.assertEqual(response.status_code, 200)
        upload_file.assert_called_once()
        conn = server.db_conn()
        task = conn.execute("SELECT payload_json,scene_id FROM tasks ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        saved = json.loads(task["payload_json"])
        self.assertEqual(task["scene_id"], "reference")
        self.assertEqual(saved["reference_images"][0]["object"], "uploads/canvas-reference.png")

    def test_video_generation_rejects_multipart_input_reference_when_uploads_forbidden(self):
        original_cfg = server.CFG
        server.CFG = server.deep_merge(
            original_cfg,
            {
                "gateway": {
                    "scene_policies": {
                        "reference": {
                            "enabled": True,
                            "experimental": False,
                            "verification_status": "live_verified",
                            "risk_level": "low",
                        }
                    }
                }
            },
        )
        try:
            response = self.client.post(
                "/v1/videos",
                headers={"Authorization": "Bearer openai-video-key"},
                data={
                    "model": "sora-2",
                    "prompt": "multipart references",
                    "seconds": "5",
                    "size": "1280x720",
                },
                files=[("input_reference", ("ref-image.png", b"pngdata", "image/png"))],
            )
        finally:
            server.CFG = original_cfg

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["type"], "invalid_request_error")
        self.assertEqual(response.json()["error"]["code"], "api_key_upload_forbidden")

    def test_video_generation_rejects_invalid_input_reference(self):
        original_cfg = server.CFG
        server.CFG = server.deep_merge(
            original_cfg,
            {
                "gateway": {
                    "scene_policies": {
                        "reference": {
                            "enabled": True,
                            "experimental": False,
                            "verification_status": "live_verified",
                            "risk_level": "low",
                        }
                    }
                }
            },
        )
        try:
            response = self.client.post(
                "/v1/videos",
                headers={"Authorization": "Bearer openai-video-upload-key"},
                json={
                    "model": "sora-2",
                    "prompt": "bad references",
                    "seconds": "5",
                    "size": "1280x720",
                    "input_reference": [{"fileName": "missing-object", "fileExt": "png"}],
                },
            )
        finally:
            server.CFG = original_cfg

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["param"], "input_reference")
        self.assertEqual(response.json()["error"]["code"], "invalid_input_reference")

    def test_video_generation_rejects_unowned_json_input_reference(self):
        original_cfg = server.CFG
        server.CFG = server.deep_merge(
            original_cfg,
            {
                "gateway": {
                    "scene_policies": {
                        "reference": {
                            "enabled": True,
                            "experimental": False,
                            "verification_status": "live_verified",
                            "risk_level": "low",
                        }
                    }
                }
            },
        )
        try:
            response = self.client.post(
                "/v1/videos",
                headers={"Authorization": "Bearer openai-video-upload-key"},
                json={
                    "model": "sora-2",
                    "prompt": "foreign references",
                    "seconds": "5",
                    "size": "1280x720",
                    "input_reference": [{"object": "uploads/not-owned.png"}],
                },
            )
        finally:
            server.CFG = original_cfg

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["param"], "input_reference")
        self.assertEqual(response.json()["error"]["code"], "invalid_input_reference")

    def test_video_retrieve_maps_native_task_status_without_leaking_internals(self):
        task_id = self.seed_video_task(status="hydrating")

        response = self.client.get(
            f"/v1/videos/video_{task_id}",
            headers={"Authorization": "Bearer openai-video-key"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["id"], f"video_{task_id}")
        self.assertEqual(payload["status"], "in_progress")
        self.assertEqual(payload["progress"], 75)
        self.assertEqual(payload["model"], "sora-2")
        self.assertNotIn("account_id", payload)
        self.assertNotIn("response", payload)

    def test_video_content_redirects_only_for_owned_completed_job(self):
        asset = "https://cdn.oreateai.com/aivideo/result.mp4"
        task_id = self.seed_video_task(status="completed", assets=[asset])

        response = self.client.get(
            f"/v1/videos/video_{task_id}/content",
            headers={"Authorization": "Bearer openai-video-key"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], asset)

        foreign = self.client.get(
            f"/v1/videos/video_{task_id}/content",
            headers={"Authorization": "Bearer openai-model-key"},
            follow_redirects=False,
        )
        self.assertEqual(foreign.status_code, 404)
        self.assertEqual(foreign.json()["error"]["type"], "invalid_request_error")

    def test_video_content_rejects_non_completed_and_untrusted_assets(self):
        queued_id = self.seed_video_task(status="queued")
        queued = self.client.get(
            f"/v1/videos/video_{queued_id}/content",
            headers={"Authorization": "Bearer openai-video-key"},
            follow_redirects=False,
        )
        self.assertEqual(queued.status_code, 409)
        self.assertEqual(queued.json()["error"]["code"], "video_not_completed")

        unsafe_id = self.seed_video_task(status="completed", assets=["https://evil.example/result.mp4"])
        unsafe = self.client.get(
            f"/v1/videos/video_{unsafe_id}/content",
            headers={"Authorization": "Bearer openai-video-key"},
            follow_redirects=False,
        )
        self.assertEqual(unsafe.status_code, 502)
        self.assertEqual(unsafe.json()["error"]["code"], "invalid_video_asset")

    def test_video_delete_cancels_active_job_but_preserves_completed_job(self):
        queued_id = self.seed_video_task(status="queued")
        cancelled = self.client.delete(
            f"/v1/videos/video_{queued_id}",
            headers={"Authorization": "Bearer openai-video-key"},
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "cancelled")

        completed_id = self.seed_video_task(status="completed", assets=["https://cdn.oreateai.com/aivideo/result.mp4"])
        completed = self.client.delete(
            f"/v1/videos/video_{completed_id}",
            headers={"Authorization": "Bearer openai-video-key"},
        )
        self.assertEqual(completed.status_code, 409)
        self.assertEqual(completed.json()["error"]["code"], "video_not_cancellable")

    def test_video_delete_cancels_legacy_active_job_owned_through_usage_log(self):
        owner_key_id = self.api_key_id("openai-video-key")
        task_id = self.seed_video_task(status="running")
        conn = server.db_conn()
        conn.execute("UPDATE tasks SET api_key_id=NULL WHERE id=?", (task_id,))
        conn.commit()
        conn.close()
        server.log_usage(
            owner_key_id,
            "video",
            1,
            "video prompt",
            "running",
            task_id=task_id,
            status_code=202,
        )

        foreign = self.client.delete(
            f"/v1/videos/video_{task_id}",
            headers={"Authorization": "Bearer openai-model-key"},
        )
        self.assertEqual(foreign.status_code, 404)

        cancelled = self.client.delete(
            f"/v1/videos/video_{task_id}",
            headers={"Authorization": "Bearer openai-video-key"},
        )

        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "cancelled")
        conn = server.db_conn()
        task_row = conn.execute(
            "SELECT api_key_id,status FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        usage_row = conn.execute(
            "SELECT api_key_id,status,error_code FROM usage_log WHERE task_id=?",
            (task_id,),
        ).fetchone()
        conn.close()
        self.assertIsNone(task_row["api_key_id"])
        self.assertEqual(task_row["status"], "cancelled")
        self.assertEqual(usage_row["api_key_id"], owner_key_id)
        self.assertEqual(usage_row["status"], "cancelled")
        self.assertEqual(usage_row["error_code"], "TASK_CANCELLED")


if __name__ == "__main__":
    unittest.main()
