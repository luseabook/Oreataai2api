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
            patch.object(server.CLIENT, "create_chat", return_value={"data": {"chatId": "chat-invalid"}}) as create_chat,
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

    def test_generate_records_model_parameters_and_estimated_cost(self):
        self.seed_account_with_capabilities()
        key_id = self.seed_api_key("cost-key")

        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "create_chat", return_value={"data": {"chatId": "chat-cost"}}),
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
            patch.object(server.CLIENT, "create_chat", return_value={"data": {"chatId": "chat-idem"}}) as create_chat,
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
            patch.object(server.CLIENT, "create_chat", return_value={"data": {"chatId": "chat-idem"}}),
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
            patch.object(server.CLIENT, "create_chat", return_value={"data": {"chatId": "chat-rate"}}),
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
            patch.object(server.CLIENT, "create_chat", return_value={"data": {"chatId": "chat-daily"}}),
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
            patch.object(server.CLIENT, "create_chat", return_value={"data": {"chatId": "chat-point"}}) as create_chat,
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
            patch.object(server.CLIENT, "create_chat", return_value={"data": {"chatId": "chat-ready"}}),
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
            patch.object(server.CLIENT, "create_chat", side_effect=RuntimeError("upstream down")),
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
            patch.object(server.CLIENT, "create_chat", return_value={"data": {"chatId": "chat-success"}}),
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
            patch.object(server.CLIENT, "create_chat", return_value={"data": {"chatId": "chat-detail"}}),
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
