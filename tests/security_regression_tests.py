import json
import hashlib
import io
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
import zipfile

from fastapi.testclient import TestClient

import server

TEST_ENCRYPTION_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


class SecurityRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "accounts.db"
        self.config_path = Path(self.tmp.name) / "config.json"
        self.db_patch = patch.object(server, "DB_PATH", self.db_path)
        self.db_patch.start()
        self.config_patch = patch.object(server, "CONFIG_PATH", self.config_path)
        self.config_patch.start()
        base_cfg = json.loads(json.dumps(server.CFG))
        self.cfg_patch = patch.object(
            server,
            "CFG",
            server.deep_merge(
                base_cfg,
                {
                    "server": {
                        "host": "127.0.0.1",
                        "admin_username": "admin",
                        "admin_password": "test-admin-password",
                        "encryption_key": TEST_ENCRYPTION_KEY,
                    },
                    "gateway": {"enable_background_worker": False},
                },
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

    def test_readyz_rejects_placeholder_admin_password(self):
        original_cfg = server.CFG
        server.CFG = server.deep_merge(
            original_cfg,
            {"server": {"admin_username": "admin", "admin_password": "CHANGE_ME"}},
        )
        try:
            response = self.client.get("/readyz")
        finally:
            server.CFG = original_cfg

        self.assertEqual(response.status_code, 503)
        self.assertIn("administrator", response.json()["detail"])

    def test_readyz_requires_encryption_key_when_account_secrets_exist(self):
        server.save_account(
            "encrypted@example.com",
            "plain-password",
            server.OreateSession(
                email="encrypted@example.com",
                password="plain-password",
                cookies={"OUID": "encrypted-ouid", "ouss": "encrypted-ouss"},
            ),
            model_info={},
            video_info={},
            status="verified",
            source="manual",
        )
        original_cfg = server.CFG
        server.CFG = server.deep_merge(original_cfg, {"server": {"encryption_key": ""}})
        try:
            with patch.dict(server.os.environ, {"OREATE_ENCRYPTION_KEY": ""}):
                response = self.client.get("/readyz")
        finally:
            server.CFG = original_cfg

        self.assertEqual(response.status_code, 503)
        self.assertIn("encryption key", response.json()["detail"])

    def test_readyz_sanitizes_database_failures(self):
        with patch.object(server, "db_conn", side_effect=RuntimeError("accounts.db is locked at C:/secret/path")):
            response = self.client.get("/readyz")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "database not ready")
        self.assertNotIn("secret/path", response.text)

    def test_readyz_rejects_plaintext_account_secrets(self):
        self.seed_account()

        response = self.client.get("/readyz")

        self.assertEqual(response.status_code, 503)
        self.assertIn("plaintext account secrets", response.json()["detail"])

    def test_readyz_rejects_public_bind_without_explicit_proxy_tls_acknowledgement(self):
        account_id = self.seed_account()
        conn = server.db_conn()
        conn.execute(
            "UPDATE accounts SET model_info_json=?, video_info_json=? WHERE id=?",
            (json.dumps(self.sample_image_info()), json.dumps(self.sample_video_info()), account_id),
        )
        conn.commit()
        conn.close()
        server.init_db()
        original_cfg = server.CFG
        server.CFG = server.deep_merge(
            original_cfg,
            {
                "server": {"host": "0.0.0.0"},
                "deployment": {
                    "allow_public_bind": False,
                    "trust_reverse_proxy": False,
                    "tls_terminated_by_proxy": False,
                },
            },
        )
        try:
            response = self.client.get("/readyz")
        finally:
            server.CFG = original_cfg

        self.assertEqual(response.status_code, 503)
        self.assertIn("public bind", response.json()["detail"])

    def test_readyz_allows_public_bind_with_explicit_proxy_tls_acknowledgement(self):
        account_id = self.seed_account()
        conn = server.db_conn()
        conn.execute(
            "UPDATE accounts SET model_info_json=?, video_info_json=? WHERE id=?",
            (json.dumps(self.sample_image_info()), json.dumps(self.sample_video_info()), account_id),
        )
        conn.commit()
        conn.close()
        server.init_db()
        original_cfg = server.CFG
        server.CFG = server.deep_merge(
            original_cfg,
            {
                "server": {"host": "0.0.0.0"},
                "deployment": {
                    "allow_public_bind": True,
                    "trust_reverse_proxy": True,
                    "tls_terminated_by_proxy": True,
                },
            },
        )
        try:
            response = self.client.get("/readyz")
        finally:
            server.CFG = original_cfg

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")

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

    def test_account_sensitive_fields_are_encrypted_at_rest(self):
        server.save_account(
            "secure@example.com",
            "plain-password",
            server.OreateSession(
                email="secure@example.com",
                password="plain-password",
                cookies={"OUID": "ouid-secret", "ouss": "ouss-secret"},
            ),
            model_info={},
            video_info={},
            status="verified",
            source="manual",
        )

        conn = server.db_conn()
        row = conn.execute("SELECT password,ouid,ouss FROM accounts WHERE email=?", ("secure@example.com",)).fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertNotEqual(row["password"], "plain-password")
        self.assertNotEqual(row["ouid"], "ouid-secret")
        self.assertNotEqual(row["ouss"], "ouss-secret")

    def test_plaintext_account_secrets_are_migrated_when_encryption_is_available(self):
        now = time.time()
        conn = server.db_conn()
        conn.execute(
            """
            INSERT INTO accounts(email,password,status,source,ouid,ouss,model_info_json,video_info_json,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "legacy@example.com",
                "legacy-password",
                "verified",
                "manual",
                "legacy-ouid",
                "legacy-ouss",
                "{}",
                "{}",
                now,
                now,
            ),
        )
        conn.commit()
        conn.close()

        server.init_db()

        conn = server.db_conn()
        row = conn.execute("SELECT * FROM accounts WHERE email=?", ("legacy@example.com",)).fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertNotEqual(row["password"], "legacy-password")
        self.assertNotEqual(row["ouid"], "legacy-ouid")
        self.assertNotEqual(row["ouss"], "legacy-ouss")

        session = server.CLIENT.session_from_account(row)
        self.assertEqual(session.cookies.get("OUID"), "legacy-ouid")
        self.assertEqual(session.cookies.get("ouss"), "legacy-ouss")

    def test_admin_backup_database_does_not_contain_plaintext_account_secrets(self):
        server.save_account(
            "backup-safe@example.com",
            "plain-password",
            server.OreateSession(
                email="backup-safe@example.com",
                password="plain-password",
                cookies={"OUID": "backup-ouid", "ouss": "backup-ouss"},
            ),
            model_info={},
            video_info={},
            status="verified",
            source="manual",
        )

        response = self.client.get("/api/admin/backup", headers=self.admin_headers())

        self.assertEqual(response.status_code, 200)
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        db_bytes = archive.read("accounts.db")
        self.assertNotIn(b"plain-password", db_bytes)
        self.assertNotIn(b"backup-ouid", db_bytes)
        self.assertNotIn(b"backup-ouss", db_bytes)

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
            ("post", "/api/admin/logout", None),
            ("get", "/api/admin/audit-logs", None),
            ("get", "/api/admin/backup", None),
            ("post", "/api/admin/restore", {"confirm": "true"}),
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

    def test_admin_logout_revokes_current_session(self):
        headers = self.admin_headers()

        logout_response = self.client.post("/api/admin/logout", headers=headers)
        self.assertEqual(logout_response.status_code, 200)
        self.assertTrue(logout_response.json()["ok"])

        second_response = self.client.get("/api/admin/settings", headers=headers)
        self.assertEqual(second_response.status_code, 401)

    def test_admin_audit_log_records_admin_actions(self):
        headers = self.admin_headers()
        create_response = self.client.post(
            "/api/admin/clients",
            headers=headers,
            json={"name": "Audit Corp", "contact": "audit@example.com"},
        )
        self.assertEqual(create_response.status_code, 200)

        audit_response = self.client.get("/api/admin/audit-logs", headers=headers)
        self.assertEqual(audit_response.status_code, 200)
        items = audit_response.json()["items"]
        self.assertTrue(any(item["path"] == "/api/admin/clients" and item["method"] == "POST" for item in items))

    def test_admin_session_expiration_rejects_stale_token(self):
        login = self.client.post(
            "/api/admin/login",
            json={"username": server.CFG["server"]["admin_username"], "password": server.CFG["server"]["admin_password"]},
        )
        self.assertEqual(login.status_code, 200)
        token = login.json()["token"]
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        conn = server.db_conn()
        try:
            conn.execute("UPDATE admin_sessions SET expires_at=? WHERE token_hash=?", (time.time() - 1, token_hash))
            conn.commit()
        finally:
            conn.close()

        response = self.client.get("/api/admin/settings", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 401)

    def test_admin_backup_exports_config_and_database_snapshot(self):
        self.seed_account()
        headers = self.admin_headers()

        response = self.client.get("/api/admin/backup", headers=headers)

        self.assertEqual(response.status_code, 200)
        self.assertIn("application/zip", response.headers.get("content-type", ""))
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        names = set(archive.namelist())
        self.assertIn("accounts.db", names)
        self.assertIn("config.json", names)
        self.assertIn("manifest.json", names)

    def test_admin_restore_replaces_database_and_config_from_backup(self):
        original_ttl = server.CFG["server"]["admin_session_ttl_hours"]
        original_min_accounts = server.CFG["pool"]["min_accounts"]
        self.seed_account()
        headers = self.admin_headers()

        backup = self.client.get("/api/admin/backup", headers=headers)
        self.assertEqual(backup.status_code, 200)

        conn = server.db_conn()
        try:
            conn.execute(
                """
                INSERT INTO accounts(email,password,status,source,ouid,ouss,model_info_json,video_info_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "restore-extra@example.com",
                    "plain-password",
                    "verified",
                    "manual",
                    "ouid-extra",
                    "ouss-extra",
                    "{}",
                    "{}",
                    time.time(),
                    time.time(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        server.CFG["server"]["admin_session_ttl_hours"] = 99
        server.CFG["pool"]["min_accounts"] = 99
        save_response = self.client.post(
            "/api/admin/restore",
            headers=headers,
            data={"confirm": "true"},
            files={"file": ("backup.zip", backup.content, "application/zip")},
        )

        self.assertEqual(save_response.status_code, 200)
        self.assertTrue(save_response.json()["requires_relogin"])
        self.assertIn("重新登录", save_response.json()["message"])
        self.assertEqual(server.CFG["server"]["admin_session_ttl_hours"], original_ttl)
        self.assertEqual(server.CFG["pool"]["min_accounts"], original_min_accounts)
        restored_accounts = self.client.get("/api/accounts", headers=self.admin_headers()).json()["items"]
        emails = {item["email"] for item in restored_accounts}
        self.assertIn("user@example.com", emails)
        self.assertNotIn("restore-extra@example.com", emails)

    def test_admin_restore_revokes_existing_sessions_and_requires_relogin(self):
        self.seed_account()
        headers = self.admin_headers()

        backup = self.client.get("/api/admin/backup", headers=headers)
        self.assertEqual(backup.status_code, 200)

        restore = self.client.post(
            "/api/admin/restore",
            headers=headers,
            data={"confirm": "true"},
            files={"file": ("backup.zip", backup.content, "application/zip")},
        )

        self.assertEqual(restore.status_code, 200)
        self.assertTrue(restore.json()["requires_relogin"])
        self.assertIn("重新登录", restore.json()["message"])
        self.assertEqual(server.ADMIN_TOKENS, {})

        stale = self.client.get("/api/admin/settings", headers=headers)
        self.assertEqual(stale.status_code, 401)

        conn = server.db_conn()
        active_sessions = conn.execute(
            "SELECT COUNT(*) AS c FROM admin_sessions WHERE revoked_at IS NULL AND COALESCE(expires_at, 0) > ?",
            (time.time(),),
        ).fetchone()["c"]
        conn.close()
        self.assertEqual(active_sessions, 0)

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

    def test_admin_settings_reject_invalid_numeric_values(self):
        headers = self.admin_headers()
        baseline_cfg = json.loads(json.dumps(server.CFG))
        invalid_updates = [
            {"server": {"port": 0}},
            {"server": {"port": 65536}},
            {"server": {"port": 8890.5}},
            {"server": {"port": "8890"}},
            {"server": {"port": True}},
            {"pool": {"min_accounts": -1}},
            {"pool": {"min_accounts": True}},
            {"pool": {"min_accounts": 3.5}},
            {"pool": {"min_accounts": "3"}},
            {"pool": {"maintain_target": -1}},
            {"pool": {"maintain_target": False}},
            {"pool": {"maintain_target": 5.5}},
            {"pool": {"maintain_target": "5"}},
        ]

        for update in invalid_updates:
            with self.subTest(update=update):
                server.CFG = json.loads(json.dumps(baseline_cfg))
                server.save_config(server.CFG)
                saved_before = self.config_path.read_bytes()

                response = self.client.put(
                    "/api/admin/settings",
                    headers=headers,
                    json=update,
                )

                self.assertEqual(response.status_code, 422)
                self.assertEqual(server.CFG, baseline_cfg)
                self.assertEqual(self.config_path.read_bytes(), saved_before)

    def test_admin_settings_reject_null_numeric_values(self):
        headers = self.admin_headers()
        baseline_cfg = json.loads(json.dumps(server.CFG))

        for update in (
            {"server": {"port": None}},
            {"pool": {"min_accounts": None}},
            {"pool": {"maintain_target": None}},
        ):
            with self.subTest(update=update):
                server.CFG = json.loads(json.dumps(baseline_cfg))
                server.save_config(server.CFG)
                saved_before = self.config_path.read_bytes()

                response = self.client.put(
                    "/api/admin/settings",
                    headers=headers,
                    json=update,
                )

                self.assertEqual(response.status_code, 422)
                self.assertEqual(server.CFG, baseline_cfg)
                self.assertEqual(self.config_path.read_bytes(), saved_before)

    def test_admin_settings_reject_null_sections(self):
        headers = self.admin_headers()
        baseline_cfg = json.loads(json.dumps(server.CFG))

        for update in ({"server": None}, {"pool": None}):
            with self.subTest(update=update):
                server.CFG = json.loads(json.dumps(baseline_cfg))
                server.save_config(server.CFG)
                saved_before = self.config_path.read_bytes()

                response = self.client.put(
                    "/api/admin/settings",
                    headers=headers,
                    json=update,
                )

                self.assertEqual(response.status_code, 422)
                self.assertEqual(server.CFG, baseline_cfg)
                self.assertEqual(self.config_path.read_bytes(), saved_before)

    def test_admin_settings_preserves_unknown_null_fields(self):
        response = self.client.put(
            "/api/admin/settings",
            headers=self.admin_headers(),
            json={
                "server": {"custom_nullable_option": None},
                "pool": {"custom_nullable_option": None},
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("custom_nullable_option", server.CFG["server"])
        self.assertIsNone(server.CFG["server"]["custom_nullable_option"])
        self.assertIn("custom_nullable_option", server.CFG["pool"])
        self.assertIsNone(server.CFG["pool"]["custom_nullable_option"])
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertIn("custom_nullable_option", saved["server"])
        self.assertIsNone(saved["server"]["custom_nullable_option"])
        self.assertIn("custom_nullable_option", saved["pool"])
        self.assertIsNone(saved["pool"]["custom_nullable_option"])

    def test_admin_settings_reject_inconsistent_pool_targets(self):
        headers = self.admin_headers()
        baseline_cfg = json.loads(json.dumps(server.CFG))

        for update in (
            {"pool": {"min_accounts": baseline_cfg["pool"]["maintain_target"] + 1}},
            {"pool": {"maintain_target": baseline_cfg["pool"]["min_accounts"] - 1}},
        ):
            with self.subTest(update=update):
                server.CFG = json.loads(json.dumps(baseline_cfg))
                server.save_config(server.CFG)
                saved_before = self.config_path.read_bytes()

                response = self.client.put(
                    "/api/admin/settings",
                    headers=headers,
                    json=update,
                )

                self.assertEqual(response.status_code, 422)
                self.assertEqual(server.CFG, baseline_cfg)
                self.assertEqual(self.config_path.read_bytes(), saved_before)

    def test_admin_settings_reports_restart_requirement(self):
        headers = self.admin_headers()
        current_port = server.CFG["server"]["port"]

        port_response = self.client.put(
            "/api/admin/settings",
            headers=headers,
            json={
                "server": {"port": current_port + 1, "custom_server_option": "kept"},
                "custom_section": {"enabled": True},
            },
        )

        self.assertEqual(port_response.status_code, 200)
        self.assertTrue(port_response.json()["restart_required"])
        self.assertEqual(server.CFG["server"]["custom_server_option"], "kept")
        self.assertEqual(server.CFG["custom_section"], {"enabled": True})

        pool_response = self.client.put(
            "/api/admin/settings",
            headers=headers,
            json={
                "pool": {
                    "min_accounts": 4,
                    "maintain_target": 6,
                    "custom_pool_option": "kept",
                }
            },
        )

        self.assertEqual(pool_response.status_code, 200)
        self.assertFalse(pool_response.json()["restart_required"])
        self.assertEqual(server.CFG["pool"]["custom_pool_option"], "kept")

    def test_admin_settings_updates_are_serialized_without_lost_fields(self):
        server.save_config(server.CFG)
        first_save_entered = threading.Event()
        release_first_save = threading.Event()
        call_count = 0
        call_count_lock = threading.Lock()
        original_save_config = server.save_config

        def observed_save_config(candidate):
            nonlocal call_count
            with call_count_lock:
                call_count += 1
                current_call = call_count
            if current_call == 1:
                first_save_entered.set()
                self.assertTrue(release_first_save.wait(5), "timed out releasing the first settings save")
            return original_save_config(candidate)

        server_update = server.SettingsIn.model_validate({"server": {"concurrent_server_option": "kept"}})
        pool_update = server.SettingsIn.model_validate({"pool": {"concurrent_pool_option": "kept"}})

        with patch.object(server, "save_config", side_effect=observed_save_config):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(server.put_settings, server_update, None)
                self.assertTrue(first_save_entered.wait(5), "the first settings save never started")
                probe_result = []

                def probe_config_lock():
                    acquired = server.CONFIG_LOCK.acquire(blocking=False)
                    probe_result.append(acquired)
                    if acquired:
                        server.CONFIG_LOCK.release()

                probe = threading.Thread(target=probe_config_lock)
                probe.start()
                probe.join(timeout=5)
                try:
                    self.assertFalse(probe.is_alive(), "the config lock probe did not finish")
                    self.assertEqual(
                        probe_result,
                        [False],
                        "put_settings must hold CONFIG_LOCK across merge and persistence",
                    )
                    second = executor.submit(server.put_settings, pool_update, None)
                finally:
                    release_first_save.set()
                first.result(timeout=5)
                second.result(timeout=5)

        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(server.CFG["server"]["concurrent_server_option"], "kept")
        self.assertEqual(server.CFG["pool"]["concurrent_pool_option"], "kept")
        self.assertEqual(saved, server.CFG)

    def test_save_config_replaces_atomically_and_cleans_failed_temp_file(self):
        server.save_config(server.CFG)
        original_bytes = self.config_path.read_bytes()
        candidate = server.deep_merge(server.CFG, {"server": {"port": server.CFG["server"]["port"] + 1}})

        with patch.object(server.os, "replace", side_effect=OSError("simulated replace failure")):
            with self.assertRaisesRegex(OSError, "simulated replace failure"):
                server.save_config(candidate)

        self.assertEqual(self.config_path.read_bytes(), original_bytes)
        self.assertEqual(list(self.config_path.parent.glob(f".{self.config_path.name}.*.tmp")), [])

    def test_admin_html_validates_numeric_settings_and_formats_api_errors(self):
        html = server.ADMIN_HTML

        self.assertIn('<input id="s-port" type="number" min="1" max="65535" step="1"', html)
        self.assertIn('<input id="s-min" type="number" min="0" step="1"', html)
        self.assertIn('<input id="s-target" type="number" min="0" step="1"', html)
        self.assertIn("function requiredIntegerValue", html)
        self.assertIn("function formatApiError", html)
        self.assertIn("restart_required", html)
        self.assertIn("maintainTarget < minAccounts", html)
        self.assertIn("s.pool?.min_accounts??3", html)
        self.assertIn("s.pool?.maintain_target??5", html)
        self.assertIn("已保存但刷新失败", html)

    def test_admin_html_javascript_helpers_execute_in_node(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required to execute the admin JavaScript regression tests")
        script = server.ADMIN_HTML.split("<script>", 1)[1].split("</script>", 1)[0]
        node_program = f"""
const source = {json.dumps(script)};
function extractFunction(name) {{
  const marker = `function ${{name}}(`;
  const start = source.indexOf(marker);
  if (start < 0) throw new Error(`missing function ${{name}}`);
  const braceStart = source.indexOf('{{', start);
  let depth = 0;
  for (let index = braceStart; index < source.length; index += 1) {{
    if (source[index] === '{{') depth += 1;
    if (source[index] === '}}') {{
      depth -= 1;
      if (depth === 0) return source.slice(start, index + 1);
    }}
  }}
  throw new Error(`unterminated function ${{name}}`);
}}
const values = Object.create(null);
const document = {{
  getElementById(id) {{
    return {{value: values[id]}};
  }},
}};
const helpers = new Function(
  'document',
  `${{extractFunction('requiredIntegerValue')}}
${{extractFunction('formatApiError')}}
return {{requiredIntegerValue, formatApiError}};`,
)(document);
function assertEqual(actual, expected, label) {{
  if (actual !== expected) throw new Error(`${{label}}: expected ${{JSON.stringify(expected)}}, got ${{JSON.stringify(actual)}}`);
}}
function assertThrows(callback, label) {{
  let threw = false;
  try {{ callback(); }} catch (error) {{ threw = true; }}
  if (!threw) throw new Error(`${{label}}: expected an exception`);
}}
values.number = '';
assertThrows(() => helpers.requiredIntegerValue('number', 'value', 0), 'blank');
values.number = '1.5';
assertThrows(() => helpers.requiredIntegerValue('number', 'value', 0), 'decimal');
values.number = '65536';
assertThrows(() => helpers.requiredIntegerValue('number', 'value', 1, 65535), 'out of range');
values.number = '0';
assertEqual(helpers.requiredIntegerValue('number', 'value', 0), 0, 'zero');
values.number = '12';
assertEqual(helpers.requiredIntegerValue('number', 'value', 0), 12, 'positive integer');
assertEqual(
  helpers.formatApiError({{detail: [{{loc: ['body', 'server', 'port'], msg: 'invalid port'}}]}}),
  'server.port: invalid port',
  'Pydantic validation detail',
);
assertEqual(
  helpers.formatApiError({{error: {{message: 'request conflict'}}}}),
  'request conflict',
  'gateway error message',
);
assertEqual(helpers.formatApiError({{message: 'plain failure'}}), 'plain failure', 'top-level message');
assertEqual(helpers.formatApiError({{}}, 'unauthorized'), 'unauthorized', '401 fallback');
"""
        node_test_path = Path(self.tmp.name) / "admin_helpers_test.js"
        node_test_path.write_text(node_program, encoding="utf-8")
        completed = subprocess.run(
            [node, str(node_test_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_admin_html_javascript_parses_in_node(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required to parse the admin JavaScript regression tests")
        script = server.ADMIN_HTML.split("<script>", 1)[1].split("</script>", 1)[0]
        node_script_path = Path(self.tmp.name) / "admin_script.js"
        node_script_path.write_text(script, encoding="utf-8")

        completed = subprocess.run(
            [node, "--check", str(node_script_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_admin_html_raw_source_script_parses_in_node(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required to parse the raw embedded admin script")
        source = Path(server.__file__).read_text(encoding="utf-8")
        html_match = re.search(r'ADMIN_HTML\s*=\s*"""(.*?)"""', source, flags=re.DOTALL)
        self.assertIsNotNone(html_match, "ADMIN_HTML source literal was not found")
        raw_html = html_match.group(1)
        raw_script = raw_html.split("<script>", 1)[1].split("</script>", 1)[0]
        raw_script_path = Path(self.tmp.name) / "admin_raw_source_script.js"
        raw_script_path.write_text(raw_script, encoding="utf-8")
        runner_path = Path(self.tmp.name) / "parse_admin_raw_source.js"
        runner_path.write_text(
            "const fs=require('fs'); new Function(fs.readFileSync(process.argv[2], 'utf8'));",
            encoding="utf-8",
        )

        completed = subprocess.run(
            [node, str(runner_path), str(raw_script_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

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
        self.assertNotIn("point_balance_json", account)
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
        account_id = self.seed_account()
        conn = server.db_conn()
        conn.execute(
            "UPDATE accounts SET model_info_json=?, video_info_json=? WHERE id=?",
            (json.dumps(self.sample_image_info()), json.dumps(self.sample_video_info()), account_id),
        )
        conn.commit()
        conn.close()

        class StubClient:
            def session_from_account(self, account):
                return object()

            def create_chat_session(self, session, chat_type):
                return {"chatId": "chat-auto", "focusId": "focus-auto"}

            def stream_generation(self, *args, **kwargs):
                return {"events": [{"event": "end"}], "error": None}

            def hydrate_generation_result(self, session, chat_id):
                return {"raw": {}, "assets": []}

        with patch.object(server, "CLIENT", StubClient()):
            response = self.client.post(
                "/api/media/generate",
                headers=self.admin_headers(),
                json={"kind": "image", "prompt": "hello"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_delete_api_key_soft_deletes_without_dropping_usage_log(self):
        account_id = self.seed_account()
        now = time.time()
        conn = server.db_conn()
        conn.execute("INSERT INTO api_keys(key,name,enabled,created_at) VALUES(?,?,1,?)", ("audit-key", "audit", now))
        key_id = conn.execute("SELECT id FROM api_keys WHERE key='audit-key'").fetchone()[0]
        conn.execute(
            "INSERT INTO usage_log(api_key_id,kind,account_id,prompt,status,response_summary,created_at) VALUES(?,?,?,?,?,?,?)",
            (key_id, "image", account_id, "hello", "queued", "queued", now),
        )
        conn.commit()
        conn.close()

        response = self.client.delete(f"/api/admin/apikeys/{key_id}", headers=self.admin_headers())
        self.assertEqual(response.status_code, 200)

        conn = server.db_conn()
        key_row = conn.execute("SELECT enabled,deleted_at,disabled_reason FROM api_keys WHERE id=?", (key_id,)).fetchone()
        usage_count = conn.execute("SELECT COUNT(*) AS c FROM usage_log WHERE api_key_id=?", (key_id,)).fetchone()["c"]
        conn.close()
        self.assertEqual(key_row["enabled"], 0)
        self.assertIsNotNone(key_row["deleted_at"])
        self.assertEqual(key_row["disabled_reason"], "deleted")
        self.assertEqual(usage_count, 1)

    def test_register_endpoint_redacts_passwords_and_mail_tokens(self):
        fake_result = {
            "ok": True,
            "status": "verified",
            "account_id": 1,
            "email": "user@example.com",
            "password": "Aa1@secret123",
            "signup_status": 200,
            "signup_response": {"status": {"code": 0}},
            "verification": {
                "confirm": {
                    "status_code": 200,
                    "cookies": {"OUID": "confirm-ouid", "ouss": "confirm-ouss"},
                    "accessToken": "confirm-access-token",
                    "session": "confirm-session",
                }
            },
            "verification_artifact": {
                "link": "https://www.oreateai.com/passport/confirm?tokenID=abc123",
                "code": "abc123",
            },
            "trace": [
                {
                    "step": "extract_token_from_link",
                    "tokenID": "abc123",
                    "cookie": "trace-cookie-secret",
                    "cookies": {"OUID": "trace-ouid", "ouss": "trace-ouss"},
                    "sessionkey": "trace-session-key",
                }
            ],
            "mailbox": {"address": "user@example.com", "token": "mail-token"},
            "cookies": {"OUID": "root-ouid", "ouss": "root-ouss"},
        }

        with patch.object(server, "auto_register_accounts", return_value=[fake_result]):
            response = self.client.post("/api/register/one", headers=self.admin_headers())

        self.assertEqual(response.status_code, 200)
        item = response.json()["items"][0]
        self.assertEqual(item["email"], "user@example.com")
        self.assertNotIn("password", item)
        self.assertEqual(item["mailbox"]["address"], "user@example.com")
        self.assertNotIn("token", item["mailbox"])
        self.assertEqual(item["verification_artifact"]["link"], server.SECRET_PLACEHOLDER)
        self.assertEqual(item["verification_artifact"]["code"], server.SECRET_PLACEHOLDER)
        self.assertEqual(item["trace"][0]["tokenID"], server.SECRET_PLACEHOLDER)
        body_text = json.dumps(item, ensure_ascii=False)
        for secret in (
            "Aa1@secret123",
            "mail-token",
            "abc123",
            "confirm-ouid",
            "confirm-ouss",
            "confirm-access-token",
            "confirm-session",
            "trace-cookie-secret",
            "trace-ouid",
            "trace-ouss",
            "trace-session-key",
            "root-ouid",
            "root-ouss",
        ):
            self.assertNotIn(secret, body_text)
        self.assertIn(server.SECRET_PLACEHOLDER, body_text)

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
        self.assertIn("verification_status", html)
        self.assertIn("experimental", html)
        self.assertIn("retryTask", html)
        self.assertIn("cancelTask", html)
        self.assertIn("hydrateTask", html)
        self.assertIn("task-preview", html)
        self.assertNotIn("s-admin-pwd", html)

    def test_admin_html_escapes_untrusted_usage_account_and_client_values(self):
        html = server.ADMIN_HTML

        self.assertIn("${escapeHtml((u.prompt||'').substring(0,40))}", html)
        self.assertIn("${escapeHtml(u.account_email||u.account_id||'-')}", html)
        self.assertIn("${escapeHtml(u.model_name||'-')}", html)
        self.assertIn("${escapeHtml(em)}", html)
        self.assertIn("data-copy-value=\"${escapeHtml(em)}\"", html)
        self.assertIn("${escapeHtml(c.name||'-')}", html)
        self.assertIn("${escapeHtml(c.contact||'-')}", html)
        self.assertNotIn("${(u.prompt||'').substring(0,40)}", html)

    def test_admin_page_sets_defensive_browser_headers(self):
        response = self.client.get("/admin")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        csp = response.headers["content-security-policy"]
        self.assertIn("object-src 'none'", csp)
        self.assertIn("frame-ancestors 'none'", csp)


if __name__ == "__main__":
    unittest.main()
