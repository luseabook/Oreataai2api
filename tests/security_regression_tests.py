import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import server


class SecurityRegressionTests(unittest.TestCase):
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
        server.init_db()
        self.client = TestClient(server.app)

    def tearDown(self):
        server.ADMIN_TOKENS.clear()
        self.cfg_patch.stop()
        self.config_patch.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

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

    def test_admin_login_rejects_placeholder_password(self):
        original_cfg = server.CFG
        server.CFG = server.deep_merge(
            original_cfg,
            {"server": {"admin_username": "admin", "admin_password": "admin123"}},
        )
        try:
            response = self.client.post("/api/admin/login", json={"username": "admin", "password": "admin123"})
        finally:
            server.CFG = original_cfg

        self.assertEqual(response.status_code, 500)

    def seed_account(self):
        now = time.time()
        conn = server.db_conn()
        conn.execute(
            """
            INSERT INTO accounts(email,password,status,source,ouid,ouss,model_info_json,video_info_json,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "user@example.com",
                "plain-password",
                "verified",
                "manual",
                "ouid-secret",
                "ouss-secret",
                "{}",
                "{}",
                now,
                now,
            ),
        )
        conn.commit()
        account_id = conn.execute("SELECT id FROM accounts").fetchone()[0]
        conn.close()
        return account_id

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

    def test_management_endpoints_require_admin_token(self):
        for method, path, body in [
            ("get", "/api/admin/settings", None),
            ("put", "/api/admin/settings", {"server": {"port": 9999}}),
            ("post", "/api/admin/credentials", {"current_password": "x", "new_username": "admin", "new_password": "new-password-123", "confirm_password": "new-password-123"}),
            ("get", "/api/accounts", None),
            ("get", "/api/mail/test", None),
            ("get", "/api/models/capabilities", None),
            ("post", "/api/models/refresh", None),
            ("post", "/api/register/one", None),
            ("post", "/api/register/batch", {"count": 1}),
            ("post", "/api/accounts/import", {"email": "a@b.test", "password": "x"}),
            ("post", "/api/media/generate", {"kind": "image", "prompt": "x"}),
            ("get", "/api/tasks", None),
            ("post", "/api/tasks/1/mark", {"status": "completed"}),
            ("post", "/api/pool/maintain", {"force_register": True, "max_register": 1}),
        ]:
            caller = getattr(self.client, method)
            response = caller(path, json=body) if body is not None else caller(path)
            self.assertEqual(response.status_code, 401, f"{method.upper()} {path}")

    def test_admin_credentials_change_requires_current_password(self):
        response = self.client.post(
            "/api/admin/credentials",
            headers=self.admin_headers(),
            json={
                "current_password": "wrong",
                "new_username": "admin2",
                "new_password": "new-password-123",
                "confirm_password": "new-password-123",
            },
        )
        self.assertEqual(response.status_code, 401)

    def test_admin_credentials_change_rejects_weak_or_mismatched_password(self):
        headers = self.admin_headers()
        mismatch = self.client.post(
            "/api/admin/credentials",
            headers=headers,
            json={
                "current_password": "test-admin-password",
                "new_username": "admin2",
                "new_password": "new-password-123",
                "confirm_password": "different",
            },
        )
        weak = self.client.post(
            "/api/admin/credentials",
            headers=headers,
            json={
                "current_password": "test-admin-password",
                "new_username": "admin2",
                "new_password": "admin123",
                "confirm_password": "admin123",
            },
        )
        self.assertEqual(mismatch.status_code, 400)
        self.assertEqual(weak.status_code, 400)

    def test_admin_credentials_change_updates_config_and_invalidates_tokens(self):
        headers = self.admin_headers()
        response = self.client.post(
            "/api/admin/credentials",
            headers=headers,
            json={
                "current_password": "test-admin-password",
                "new_username": "new-admin",
                "new_password": "new-password-123",
                "confirm_password": "new-password-123",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(server.CFG["server"]["admin_username"], "new-admin")
        self.assertEqual(server.CFG["server"]["admin_password"], "new-password-123")
        old_token_response = self.client.get("/api/admin/settings", headers=headers)
        self.assertEqual(old_token_response.status_code, 401)
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["server"]["admin_username"], "new-admin")

    def test_admin_settings_update_cannot_change_credentials(self):
        headers = self.admin_headers()
        response = self.client.put(
            "/api/admin/settings",
            headers=headers,
            json={
                "server": {
                    "port": 8899,
                    "admin_username": "settings-admin",
                    "admin_password": "settings-password-123",
                }
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(server.CFG["server"]["port"], 8899)
        self.assertEqual(server.CFG["server"]["admin_username"], "admin")
        self.assertEqual(server.CFG["server"]["admin_password"], "test-admin-password")

    def test_admin_settings_response_redacts_secrets(self):
        original_cfg = server.CFG
        server.CFG = server.deep_merge(
            original_cfg,
            {"server": {"admin_password": "secret-password"}, "mail": {"api_key": "AC-secret-key"}},
        )
        try:
            response = self.client.get("/api/admin/settings", headers=self.admin_headers())
        finally:
            server.CFG = original_cfg

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotEqual(payload["server"]["admin_password"], "secret-password")
        self.assertNotEqual(payload["mail"]["api_key"], "AC-secret-key")

    def test_accounts_response_does_not_expose_credentials_or_session_cookies(self):
        self.seed_account()
        response = self.client.get("/api/accounts", headers=self.admin_headers())

        self.assertEqual(response.status_code, 200)
        account = response.json()["items"][0]
        self.assertNotIn("password", account)
        self.assertNotIn("ouss", account)
        self.assertNotIn("model_info_json", account)
        self.assertNotIn("video_info_json", account)
        self.assertEqual(account["email"], "user@example.com")

    def test_gateway_task_detail_is_scoped_to_own_api_key(self):
        account_id = self.seed_account()
        conn = server.db_conn()
        now = time.time()
        conn.execute("INSERT INTO api_keys(key,name,enabled,created_at) VALUES(?,?,1,?)", ("key-a", "a", now))
        conn.execute("INSERT INTO api_keys(key,name,enabled,created_at) VALUES(?,?,1,?)", ("key-b", "b", now))
        key_a_id = conn.execute("SELECT id FROM api_keys WHERE key='key-a'").fetchone()[0]
        conn.commit()
        conn.close()

        task_id = server.save_task(account_id, "image", "prompt", {"content": "prompt"}, {"data": {"chatId": "chat-1"}})
        conn = server.db_conn()
        conn.execute(
            "INSERT INTO usage_log(api_key_id,task_id,kind,account_id,prompt,status,response_summary,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (key_a_id, task_id, "image", account_id, "prompt", "created", "", now),
        )
        conn.commit()
        conn.close()

        other_response = self.client.get(f"/v1/task/{task_id}", headers={"Authorization": "Bearer key-b"})
        own_response = self.client.get(f"/v1/task/{task_id}", headers={"Authorization": "Bearer key-a"})

        self.assertEqual(other_response.status_code, 404)
        self.assertEqual(own_response.status_code, 200)

    def test_token_id_is_extracted_from_verification_link(self):
        link = "https://www.oreateai.com/passport/confirm?tokenID=abc123&x=1"
        self.assertEqual(server.extract_token_id_from_link(link), "abc123")

    def test_normalizes_image_and_video_capabilities(self):
        caps = server.normalize_capabilities(self.sample_image_info(), self.sample_video_info())
        self.assertEqual(caps["image"]["models"][0]["name"], "Google Nano Banana 2")
        self.assertEqual(caps["image"]["models"][0]["description"], "Flagship 4K high-resolution")
        self.assertEqual(caps["image"]["models"][0]["resolutions"], ["4K", "2K"])
        self.assertEqual(caps["image"]["models"][0]["ratios"], ["16:9", "1:1"])
        self.assertEqual(caps["video"]["models"][0]["name"], "Seedance 2.0 Mini")
        self.assertEqual(caps["video"]["models"][0]["description"], "视频模型说明")
        self.assertEqual(caps["video"]["models"][0]["durations"], [5, 10])
        self.assertEqual(caps["video"]["models"][0]["resolutions"], ["480", "720"])
        self.assertEqual(caps["video"]["models"][0]["ratios"], ["16:9", "9:16"])
        self.assertTrue(caps["video"]["models"][0]["supports_audio"])
        self.assertEqual(caps["video"]["scenes"][0]["scene_id"], "text_or_image")

    def test_gateway_capabilities_requires_api_key_and_returns_models(self):
        account_id = self.seed_account()
        conn = server.db_conn()
        now = time.time()
        conn.execute("INSERT INTO api_keys(key,name,enabled,created_at) VALUES(?,?,1,?)", ("cap-key", "cap", now))
        conn.execute(
            "UPDATE accounts SET model_info_json=?, video_info_json=? WHERE id=?",
            (json.dumps(self.sample_image_info()), json.dumps(self.sample_video_info()), account_id),
        )
        conn.commit()
        conn.close()

        unauthorized = self.client.get("/v1/capabilities")
        authorized = self.client.get("/v1/capabilities", headers={"Authorization": "Bearer cap-key"})

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(authorized.status_code, 200)
        payload = authorized.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source_account_id"], account_id)
        self.assertEqual(payload["image"]["models"][0]["name"], "Google Nano Banana 2")
        self.assertEqual(payload["video"]["models"][0]["resolutions"], ["480", "720"])

    def test_admin_model_capabilities_requires_admin(self):
        account_id = self.seed_account()
        conn = server.db_conn()
        conn.execute(
            "UPDATE accounts SET model_info_json=?, video_info_json=? WHERE id=?",
            (json.dumps(self.sample_image_info()), json.dumps(self.sample_video_info()), account_id),
        )
        conn.commit()
        conn.close()

        unauthorized = self.client.get("/api/models/capabilities")
        authorized = self.client.get("/api/models/capabilities", headers=self.admin_headers())

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(authorized.status_code, 200)
        self.assertEqual(authorized.json()["image"]["models"][0]["name"], "Google Nano Banana 2")

    def test_admin_generate_can_auto_select_account(self):
        self.seed_account()

        class StubClient:
            def session_from_account(self, account):
                return object()

            def create_chat(self, session, payload):
                return {"status": {"code": 0}, "data": {"chatId": "chat-auto"}}

        with patch.object(server, "CLIENT", StubClient()):
            response = self.client.post(
                "/api/media/generate",
                headers=self.admin_headers(),
                json={"kind": "image", "prompt": "hello"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_admin_html_sends_bearer_token_for_api_calls(self):
        html = server.ADMIN_HTML
        self.assertIn("localStorage", html)
        self.assertIn("Authorization", html)
        self.assertIn("/api/admin/login", html)

    def test_admin_html_contains_credentials_and_capability_controls(self):
        html = server.ADMIN_HTML
        self.assertIn("/api/admin/credentials", html)
        self.assertIn("/api/models/capabilities", html)
        self.assertIn("changeCredentials", html)
        self.assertIn("loadCapabilities", html)
        self.assertNotIn("s-admin-pwd", html)


if __name__ == "__main__":
    unittest.main()
