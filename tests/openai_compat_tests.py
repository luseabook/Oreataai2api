import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

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
        with self.assertRaises(OpenAICompatError) as caught:
            image_size_to_ratio("2048x2048")
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
                                "size": [{"ratio": "1:1"}, {"ratio": "16:9"}],
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
        conn.commit()
        conn.close()

    def api_key_id(self, key: str) -> int:
        conn = server.db_conn()
        row = conn.execute("SELECT id FROM api_keys WHERE key=?", (key,)).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        return int(row["id"])

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
        self.assertEqual(
            response.json()["data"],
            [{"url": "https://cdn.oreateai.com/aiimage/result.png", "revised_prompt": "a production gateway"}],
        )
        conn = server.db_conn()
        task = conn.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        self.assertEqual(task["model_name"], "Provider Image")
        self.assertEqual(task["ratio"], "1:1")
        self.assertEqual(task["resolution"], "1K")
        self.assertEqual(task["status"], "completed")

    def test_image_generation_returns_base64_for_canvas_openai_requests(self):
        source_image = b"\x89PNG\r\n\x1a\ncanvas-image"
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
                    "size": "1024x1024",
                    "n": 1,
                    "response_format": "b64_json",
                    "output_format": "png",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], "https://canvas.best")
        self.assertEqual(
            response.json()["data"],
            [
                {
                    "b64_json": base64.b64encode(source_image).decode("ascii"),
                    "revised_prompt": "a canvas integration",
                }
            ],
        )
        fetch_asset.assert_called_once_with("https://cdn.oreateai.com/aiimage/result.png")

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
        self.assertEqual(
            response.json()["data"],
            [
                {
                    "url": "https://cdn.oreateai.com/aiimage/edited.png",
                    "revised_prompt": "replace the background",
                }
            ],
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
