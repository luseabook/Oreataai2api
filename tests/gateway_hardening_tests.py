import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import server


class GatewayHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "accounts.db"
        self.config_path = Path(self.tmp.name) / "config.json"
        self.db_patch = patch.object(server, "DB_PATH", self.db_path)
        self.db_patch.start()
        self.config_patch = patch.object(server, "CONFIG_PATH", self.config_path)
        self.config_patch.start()
        self.cfg_patch = patch.object(
            server,
            "CFG",
            server.deep_merge(
                server.CFG,
                {"server": {"host": "127.0.0.1", "admin_username": "admin", "admin_password": "test-admin-password"}},
            ),
        )
        self.cfg_patch.start()
        server.ADMIN_TOKENS.clear()
        if hasattr(server, "RATE_BUCKETS"):
            server.RATE_BUCKETS.clear()
        server.init_db()
        self.client = TestClient(server.app)

    def tearDown(self):
        server.ADMIN_TOKENS.clear()
        if hasattr(server, "RATE_BUCKETS"):
            server.RATE_BUCKETS.clear()
        self.cfg_patch.stop()
        self.config_patch.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    def sample_image_info(self):
        return {
            "data": {
                "factory": [
                    {
                        "modelFactoryName": "Nano Banana",
                        "models": [
                            {
                                "modelName": "Google Nano Banana 2",
                                "modelDesc": "Flagship 4K high-resolution",
                                "modelIcon": "image.svg",
                                "resolution": ["4K", "2K"],
                                "size": [{"ratio": "16:9"}, {"ratio": "1:1"}],
                                "pointCost": [{"resolution": "4K", "point": 12}],
                            }
                        ],
                    }
                ]
            }
        }

    def sample_video_info(self):
        return {
            "models": {
                "data": {
                    "models": [
                        {
                            "modelName": "Seedance 2.0 Mini",
                            "description": {"zh": "视频模型说明", "en": "Video model"},
                            "modelIcon": "video.svg",
                            "duration": [5, 10],
                            "videoResolution": ["480", "720"],
                            "videoSize": [{"ratio": "16:9"}, {"ratio": "9:16"}],
                            "supportAudio": True,
                            "supportModifySize": True,
                            "pointCostImage": [{"duration": 5, "point": 20}],
                            "pointCostReference": [{"duration": 5, "point": 25}],
                        }
                    ]
                }
            },
            "scenes": {
                "data": {
                    "scenes": [
                        {
                            "sceneId": "text_or_image",
                            "sceneName": {"zh": "文生或图生视频", "en": "Text or image"},
                            "description": {"zh": "输入文本或图片"},
                            "sceneIcon": "scene.svg",
                        }
                    ]
                }
            },
        }

    def seed_account_with_capabilities(self, email="gateway-user@example.com"):
        now = time.time()
        conn = server.db_conn()
        conn.execute(
            """
            INSERT INTO accounts(email,password,status,source,ouid,ouss,model_info_json,video_info_json,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                email,
                "plain-password",
                "verified",
                "manual",
                "ouid-secret",
                "ouss-secret",
                json.dumps(self.sample_image_info()),
                json.dumps(self.sample_video_info()),
                now,
                now,
            ),
        )
        conn.commit()
        account_id = conn.execute("SELECT id FROM accounts WHERE email=?", (email,)).fetchone()[0]
        conn.close()
        return account_id

    def seed_api_key(self, key, rate_limit_per_minute=None, daily_request_limit=None, daily_point_limit=None):
        now = time.time()
        conn = server.db_conn()
        conn.execute("INSERT INTO api_keys(key,name,enabled,created_at) VALUES(?,?,1,?)", (key, key, now))
        conn.execute(
            """
            UPDATE api_keys
            SET rate_limit_per_minute=?, daily_request_limit=?, daily_point_limit=?
            WHERE key=?
            """,
            (rate_limit_per_minute, daily_request_limit, daily_point_limit, key),
        )
        conn.commit()
        key_id = conn.execute("SELECT id FROM api_keys WHERE key=?", (key,)).fetchone()[0]
        conn.close()
        return key_id

    def valid_image_request(self):
        return {
            "kind": "image",
            "prompt": "hello",
            "model_name": "Google Nano Banana 2",
            "resolution": "4K",
            "ratio": "16:9",
        }

    def admin_headers(self):
        response = self.client.post(
            "/api/admin/login",
            json={
                "username": server.CFG["server"]["admin_username"],
                "password": server.CFG["server"]["admin_password"],
            },
        )
        self.assertEqual(response.status_code, 200)
        return {"Authorization": f"Bearer {response.json()['token']}"}

    def test_v1_errors_use_stable_envelope(self):
        response = self.client.get("/v1/capabilities")

        self.assertEqual(response.status_code, 401)
        payload = response.json()
        self.assertIn("ok", payload)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "UNAUTHORIZED")
        self.assertIn("valid API key required", payload["error"]["message"])
        self.assertIn("request_id", payload)

    def test_v1_http_errors_use_stable_envelope_after_auth(self):
        self.seed_api_key("empty-cap-key")

        response = self.client.get("/v1/capabilities", headers={"Authorization": "Bearer empty-cap-key"})

        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertIn("ok", payload)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "SERVICE_UNAVAILABLE")
        self.assertIn("request_id", payload)

    def test_gateway_hardening_schema_is_migrated(self):
        conn = server.db_conn()
        api_key_cols = {r["name"] for r in conn.execute("PRAGMA table_info(api_keys)").fetchall()}
        account_cols = {r["name"] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()}
        usage_cols = {r["name"] for r in conn.execute("PRAGMA table_info(usage_log)").fetchall()}
        idem = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='idempotency_keys'").fetchone()
        conn.close()

        self.assertIn("rate_limit_per_minute", api_key_cols)
        self.assertIn("daily_request_limit", api_key_cols)
        self.assertIn("daily_point_limit", api_key_cols)
        self.assertIn("last_used_at", account_cols)
        self.assertIn("failure_count", account_cols)
        self.assertIn("cooldown_until", account_cols)
        self.assertIn("request_id", usage_cols)
        self.assertIn("idempotency_key", usage_cols)
        self.assertIn("model_name", usage_cols)
        self.assertIn("estimated_point_cost", usage_cols)
        self.assertIn("error_code", usage_cols)
        self.assertIn("status_code", usage_cols)
        self.assertIsNotNone(idem)

    def test_generate_rejects_invalid_video_options_before_upstream_call(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("hard-key")

        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "create_chat_session", return_value={"chatId": "chat-invalid"}) as create_chat,
        ):
            response = self.client.post(
                "/v1/generate",
                headers={"Authorization": "Bearer hard-key"},
                json={
                    "kind": "video",
                    "prompt": "hello",
                    "model_name": "Seedance 2.0 Mini",
                    "resolution": "999",
                    "ratio": "16:9",
                    "duration": 5,
                    "scene_id": "text_or_image",
                },
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "INVALID_RESOLUTION")
        create_chat.assert_not_called()

    def test_stream_generation_builds_web_image_payload(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def iter_lines(self, decode_unicode=True):
                return iter([
                    'data: {"event":"start","data":{}}',
                    'data: {"event":"end","data":{}}',
                ])

        class FakeSession:
            def __init__(self):
                self.cookies = {"OUID": "ouid-secret", "__bid_n": "bid-secret"}
                self.last_url = ""
                self.last_json = {}

            def post(self, url, **kwargs):
                self.last_url = url
                self.last_json = kwargs["json"]
                return FakeResponse()

        fake = FakeSession()
        client = server.OreateClient()
        result = client.stream_generation(
            fake,
            chat_id="chat-img",
            focus_id="focus-img",
            chat_type="aiImage",
            prompt="hello",
            image_config={"modelName": "Google Nano Banana 2", "ratio": "16:9", "resolution": "4K"},
            jt="test-jt",
        )

        self.assertTrue(fake.last_url.endswith("/oreate/sse/stream"))
        self.assertEqual(fake.last_json["chatId"], "chat-img")
        self.assertEqual(fake.last_json["focusId"], "focus-img")
        self.assertEqual(fake.last_json["messages"][0]["content"], "hello")
        self.assertEqual(fake.last_json["imageConfig"]["modelName"], "Google Nano Banana 2")
        self.assertEqual(fake.last_json["jt"], "test-jt")
        self.assertEqual(fake.last_json["js_env"], "h5")
        self.assertEqual(result["events"][-1]["event"], "end")

    def test_stream_generation_carries_banti_bid_cookie_from_helper(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def iter_lines(self, decode_unicode=True):
                return iter(['data: {"event":"end","data":{}}'])

        class FakeSession:
            def __init__(self):
                self.cookies = {"OUID": "ouid-secret"}
                self.last_json = {}

            def post(self, url, **kwargs):
                self.last_json = kwargs["json"]
                return FakeResponse()

        fake = FakeSession()
        client = server.OreateClient()
        with patch.object(
            server,
            "generate_banti_artifacts",
            return_value={"jt": "helper-jt", "cookies": {"__bid_n": "helper-bid"}},
        ):
            client.stream_generation(
                fake,
                chat_id="chat-img",
                focus_id="focus-img",
                chat_type="aiImage",
                prompt="hello",
                image_config={"modelName": "Google Nano Banana 2", "ratio": "16:9", "resolution": "4K"},
            )

        self.assertEqual(fake.cookies["__bid_n"], "helper-bid")
        self.assertEqual(fake.last_json["jt"], "helper-jt")
        self.assertEqual(fake.last_json["extra"]["bid"], "helper-bid")

    def test_stream_generation_fails_before_upstream_without_banti_bid(self):
        class FakeSession:
            def __init__(self):
                self.cookies = {"OUID": "ouid-secret"}
                self.post_called = False

            def post(self, url, **kwargs):
                self.post_called = True
                raise AssertionError("stream endpoint should not be called")

        fake = FakeSession()
        client = server.OreateClient()
        with patch.object(server, "generate_banti_artifacts", return_value={"jt": "helper-jt", "cookies": {}}):
            with self.assertRaisesRegex(RuntimeError, "banti mirror artifacts unavailable"):
                client.stream_generation(
                    fake,
                    chat_id="chat-img",
                    focus_id="focus-img",
                    chat_type="aiImage",
                    prompt="hello",
                    image_config={"modelName": "Google Nano Banana 2", "ratio": "16:9", "resolution": "4K"},
                )

        self.assertFalse(fake.post_called)

    def test_video_text_or_image_config_is_nested_like_web(self):
        config = server.build_video_config(
            {
                "model_name": "Seedance 2.0 Mini",
                "ratio": "16:9",
                "resolution": "480",
                "duration": 5,
                "scene_id": "text_or_image",
            },
            {"name": "Seedance 2.0 Mini", "ai_type": 14198},
        )

        self.assertEqual(config["scene"], "text_or_image")
        self.assertEqual(config["modelName"], "Seedance 2.0 Mini")
        self.assertEqual(config["aiType"], 14198)
        self.assertIn("textOrImage", config)
        self.assertEqual(config["textOrImage"]["image"], "")
        self.assertNotIn("sceneId", config)

    def test_user_mirror_metadata_matches_web_zce_fields(self):
        metadata = server.extract_user_mirror_metadata(
            {
                "data": {
                    "basicInfo": {"email": "user@example.test", "createTime": 1783500000},
                    "vipInfo": {"vipType": 0},
                }
            },
            fallback_email="fallback@example.test",
        )

        self.assertEqual(metadata["email"], "user@example.test")
        self.assertEqual(metadata["vip"], "0")
        self.assertEqual(metadata["reg_ts"], 1783500000)

    def test_sse_error_event_is_classified_from_nested_data_code(self):
        events = server.parse_sse_lines([
            'data: {"event":"start","data":{}}',
            'data: {"event":"error","data":{"code":200002,"msg":"params error"}}',
            'data: {"event":"end","data":{}}',
        ])

        error = server.classify_sse_error(events)

        self.assertEqual(error["code"], "200002")
        self.assertEqual(error["message"], "params error")

    def test_hydration_extracts_cdn_urls_from_markdown_messages(self):
        body = {
            "status": {"code": 0},
            "data": {
                "list": [
                    {
                        "role": "assistant",
                        "content": "done ![](https://cdn.oreateai.com/static/result/a.jpg)",
                    },
                    {
                        "role": "assistant",
                        "result": {
                            "type": "file",
                            "metadata": {
                                "files": [
                                    {"url": "https://cdn.oreateai.com/static/result/b.png"},
                                    {"bosUrl": "https://cdn.oreateai.com/static/result/c.jpeg"},
                                ]
                            },
                        },
                    },
                ]
            },
        }

        assets = server.extract_generation_assets(body)

        self.assertEqual(
            assets,
            [
                "https://cdn.oreateai.com/static/result/a.jpg",
                "https://cdn.oreateai.com/static/result/b.png",
                "https://cdn.oreateai.com/static/result/c.jpeg",
            ],
        )

    def test_hydration_extracts_extensionless_oreate_message_list_assets(self):
        body = {
            "status": {"code": 0},
            "data": {
                "messageList": [
                    {
                        "role": "assistant",
                        "content": "![](https://cdn.oreateai.com/aiimage/nano/chat-id/result-token)",
                        "data": json.dumps({
                            "imageList": ["https://cdn.oreateai.com/aiimage/nano/chat-id/result-token"]
                        }),
                    }
                ]
            },
        }

        assets = server.extract_generation_assets(body)

        self.assertEqual(assets, ["https://cdn.oreateai.com/aiimage/nano/chat-id/result-token"])

    def test_v1_generate_uses_web_generation_flow(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("web-flow-key")

        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "create_chat_session", return_value={"chatId": "chat-web", "focusId": "focus-web"}) as create_session,
            patch.object(server.CLIENT, "stream_generation", return_value={"events": [{"event": "start"}, {"event": "end"}], "error": None}) as stream_generation,
            patch.object(
                server.CLIENT,
                "hydrate_generation_result",
                return_value={
                    "raw": {
                        "status": {"code": 0},
                        "data": {"list": [{"role": "assistant", "content": "![x](https://cdn.oreateai.com/static/result/x.jpg)"}]},
                    },
                    "assets": ["https://cdn.oreateai.com/static/result/x.jpg"],
                },
            ) as hydrate,
        ):
            response = self.client.post(
                "/v1/generate",
                headers={"Authorization": "Bearer web-flow-key"},
                json=self.valid_image_request(),
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["response"]["chat"]["chatId"], "chat-web")
        self.assertEqual(payload["assets"], ["https://cdn.oreateai.com/static/result/x.jpg"])
        create_session.assert_called_once()
        stream_generation.assert_called_once()
        hydrate.assert_called_once()

    def test_session_from_account_replaces_anonymous_cookie_names(self):
        account_id = self.seed_account_with_capabilities()
        conn = server.db_conn()
        account = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        conn.close()
        session = server.requests.Session()
        session.cookies.set("OUID", "anonymous-ouid", domain="www.oreateai.com", path="/")
        session.cookies.set("ouss", "anonymous-ouss", domain="www.oreateai.com", path="/")
        client = server.OreateClient()

        with patch.object(client, "new_session", return_value=session):
            result = client.session_from_account(account)

        ouid_values = [c.value for c in result.cookies if c.name == "OUID"]
        ouss_values = [c.value for c in result.cookies if c.name == "ouss"]
        self.assertEqual(ouid_values, ["ouid-secret"])
        self.assertEqual(ouss_values, ["ouss-secret"])

    def test_generate_records_model_parameters_and_estimated_cost(self):
        self.seed_account_with_capabilities()
        key_id = self.seed_api_key("cost-key")

        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "create_chat_session", return_value={"chatId": "chat-cost"}),
            patch.object(server.CLIENT, "stream_generation", return_value={"events": [{"event": "end"}], "error": None}),
            patch.object(server.CLIENT, "hydrate_generation_result", return_value={"raw": {}, "assets": []}),
        ):
            response = self.client.post(
                "/v1/generate",
                headers={"Authorization": "Bearer cost-key"},
                json=self.valid_image_request(),
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("estimated_point_cost", response.json())
        self.assertEqual(response.json()["estimated_point_cost"], 12)
        conn = server.db_conn()
        row = conn.execute(
            "SELECT model_name,resolution,ratio,estimated_point_cost,status_code FROM usage_log WHERE api_key_id=?",
            (key_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(row["model_name"], "Google Nano Banana 2")
        self.assertEqual(row["resolution"], "4K")
        self.assertEqual(row["ratio"], "16:9")
        self.assertEqual(row["estimated_point_cost"], 12)
        self.assertEqual(row["status_code"], 200)

    def test_idempotency_key_replays_same_response_without_second_task(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("idem-key")

        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "create_chat_session", return_value={"chatId": "chat-idem"}) as create_chat,
            patch.object(server.CLIENT, "stream_generation", return_value={"events": [{"event": "end"}], "error": None}),
            patch.object(server.CLIENT, "hydrate_generation_result", return_value={"raw": {}, "assets": []}),
        ):
            first = self.client.post(
                "/v1/generate",
                headers={"Authorization": "Bearer idem-key", "Idempotency-Key": "same-1"},
                json=self.valid_image_request(),
            )
            second = self.client.post(
                "/v1/generate",
                headers={"Authorization": "Bearer idem-key", "Idempotency-Key": "same-1"},
                json=self.valid_image_request(),
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["idempotent_replay"])
        self.assertEqual(first.json()["task_id"], second.json()["task_id"])
        self.assertEqual(create_chat.call_count, 1)

    def test_idempotency_key_conflict_rejects_different_body(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("idem-conflict-key")
        changed = self.valid_image_request()
        changed["ratio"] = "1:1"

        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "create_chat_session", return_value={"chatId": "chat-idem"}),
            patch.object(server.CLIENT, "stream_generation", return_value={"events": [{"event": "end"}], "error": None}),
            patch.object(server.CLIENT, "hydrate_generation_result", return_value={"raw": {}, "assets": []}),
        ):
            first = self.client.post(
                "/v1/generate",
                headers={"Authorization": "Bearer idem-conflict-key", "Idempotency-Key": "same-2"},
                json=self.valid_image_request(),
            )
            conflict = self.client.post(
                "/v1/generate",
                headers={"Authorization": "Bearer idem-conflict-key", "Idempotency-Key": "same-2"},
                json=changed,
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["code"], "IDEMPOTENCY_KEY_CONFLICT")

    def test_api_key_rate_limit_rejects_second_request(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("rate-key", rate_limit_per_minute=1)

        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "create_chat_session", return_value={"chatId": "chat-rate"}),
            patch.object(server.CLIENT, "stream_generation", return_value={"events": [{"event": "end"}], "error": None}),
            patch.object(server.CLIENT, "hydrate_generation_result", return_value={"raw": {}, "assets": []}),
        ):
            first = self.client.post("/v1/generate", headers={"Authorization": "Bearer rate-key"}, json=self.valid_image_request())
            second = self.client.post("/v1/generate", headers={"Authorization": "Bearer rate-key"}, json=self.valid_image_request())

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["error"]["code"], "RATE_LIMITED")

    def test_daily_request_limit_rejects_second_request(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("daily-key", daily_request_limit=1)

        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "create_chat_session", return_value={"chatId": "chat-daily"}),
            patch.object(server.CLIENT, "stream_generation", return_value={"events": [{"event": "end"}], "error": None}),
            patch.object(server.CLIENT, "hydrate_generation_result", return_value={"raw": {}, "assets": []}),
        ):
            first = self.client.post("/v1/generate", headers={"Authorization": "Bearer daily-key"}, json=self.valid_image_request())
            second = self.client.post("/v1/generate", headers={"Authorization": "Bearer daily-key"}, json=self.valid_image_request())

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["error"]["code"], "DAILY_REQUEST_LIMIT_EXCEEDED")

    def test_daily_point_limit_blocks_expensive_request(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("point-key", daily_point_limit=10)

        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "create_chat_session", return_value={"chatId": "chat-point"}) as create_chat,
        ):
            response = self.client.post("/v1/generate", headers={"Authorization": "Bearer point-key"}, json=self.valid_image_request())

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"]["code"], "DAILY_POINT_LIMIT_EXCEEDED")
        create_chat.assert_not_called()

    def test_scheduler_skips_account_in_cooldown(self):
        cooling_id = self.seed_account_with_capabilities("cooling@example.com")
        ready_id = self.seed_account_with_capabilities("ready@example.com")
        self.seed_api_key("cooldown-key")
        conn = server.db_conn()
        conn.execute("UPDATE accounts SET cooldown_until=? WHERE id=?", (time.time() + 300, cooling_id))
        conn.commit()
        conn.close()

        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "create_chat_session", return_value={"chatId": "chat-ready"}),
            patch.object(server.CLIENT, "stream_generation", return_value={"events": [{"event": "end"}], "error": None}),
            patch.object(server.CLIENT, "hydrate_generation_result", return_value={"raw": {}, "assets": []}),
        ):
            response = self.client.post("/v1/generate", headers={"Authorization": "Bearer cooldown-key"}, json=self.valid_image_request())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["account_id"], ready_id)

    def test_upstream_failure_marks_account_cooldown(self):
        account_id = self.seed_account_with_capabilities()
        self.seed_api_key("fail-key")
        client = TestClient(server.app, raise_server_exceptions=False)

        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "create_chat_session", side_effect=RuntimeError("upstream down")),
        ):
            response = client.post("/v1/generate", headers={"Authorization": "Bearer fail-key"}, json=self.valid_image_request())

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "UPSTREAM_ERROR")
        conn = server.db_conn()
        row = conn.execute("SELECT failure_count,cooldown_until,last_error FROM accounts WHERE id=?", (account_id,)).fetchone()
        conn.close()
        self.assertEqual(row["failure_count"], 1)
        self.assertGreater(row["cooldown_until"], time.time())
        self.assertIn("upstream down", row["last_error"])

    def test_successful_generation_clears_account_failure_state(self):
        account_id = self.seed_account_with_capabilities()
        self.seed_api_key("success-key")
        conn = server.db_conn()
        conn.execute(
            "UPDATE accounts SET failure_count=3,cooldown_until=?,last_error=? WHERE id=?",
            (time.time() - 1, "old error", account_id),
        )
        conn.commit()
        conn.close()

        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "create_chat_session", return_value={"chatId": "chat-success"}),
            patch.object(server.CLIENT, "stream_generation", return_value={"events": [{"event": "end"}], "error": None}),
            patch.object(server.CLIENT, "hydrate_generation_result", return_value={"raw": {}, "assets": []}),
        ):
            response = self.client.post("/v1/generate", headers={"Authorization": "Bearer success-key"}, json=self.valid_image_request())

        self.assertEqual(response.status_code, 200)
        conn = server.db_conn()
        row = conn.execute("SELECT failure_count,cooldown_until,last_error,last_used_at FROM accounts WHERE id=?", (account_id,)).fetchone()
        conn.close()
        self.assertEqual(row["failure_count"], 0)
        self.assertIsNone(row["cooldown_until"])
        self.assertIsNone(row["last_error"])
        self.assertGreater(row["last_used_at"], 0)

    def test_task_detail_alias_returns_audit_fields(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("detail-key")

        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "create_chat_session", return_value={"chatId": "chat-detail"}),
            patch.object(server.CLIENT, "stream_generation", return_value={"events": [{"event": "end"}], "error": None}),
            patch.object(server.CLIENT, "hydrate_generation_result", return_value={"raw": {}, "assets": []}),
        ):
            created = self.client.post("/v1/generate", headers={"Authorization": "Bearer detail-key"}, json=self.valid_image_request())

        self.assertEqual(created.status_code, 200)
        task_id = created.json()["task_id"]
        detail = self.client.get(f"/v1/tasks/{task_id}", headers={"Authorization": "Bearer detail-key"})

        self.assertEqual(detail.status_code, 200)
        task = detail.json()["task"]
        self.assertEqual(task["id"], task_id)
        self.assertEqual(task["model_name"], "Google Nano Banana 2")
        self.assertEqual(task["resolution"], "4K")
        self.assertEqual(task["ratio"], "16:9")
        self.assertEqual(task["estimated_point_cost"], 12)

    def test_admin_html_contains_api_key_policy_and_audit_controls(self):
        html = server.ADMIN_HTML
        self.assertIn("rate_limit_per_minute", html)
        self.assertIn("daily_request_limit", html)
        self.assertIn("daily_point_limit", html)
        self.assertIn("updateApiKeyPolicy", html)
        self.assertIn("estimated_point_cost", html)
        self.assertIn("error_code", html)

    def test_admin_can_update_api_key_policy(self):
        key_id = self.seed_api_key("admin-policy-key")

        response = self.client.patch(
            f"/api/admin/apikeys/{key_id}",
            headers=self.admin_headers(),
            json={
                "rate_limit_per_minute": 7,
                "daily_request_limit": 11,
                "daily_point_limit": 13,
            },
        )

        self.assertEqual(response.status_code, 200)
        item = response.json()["item"]
        self.assertEqual(item["rate_limit_per_minute"], 7)
        self.assertEqual(item["daily_request_limit"], 11)
        self.assertEqual(item["daily_point_limit"], 13)
