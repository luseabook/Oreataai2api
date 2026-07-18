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
from unittest.mock import MagicMock, patch
import zipfile

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

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
            ("get", "/api/accounts/1/credentials", None),
            ("get", "/api/mail/test", None),
            ("get", "/api/models/capabilities", None),
            ("post", "/api/models/refresh", None),
            ("post", "/api/register/one", None),
            ("post", "/api/register/batch", {"count": 1}),
            ("post", "/api/register/jobs", {"count": 1}),
            ("get", "/api/register/jobs/1", None),
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

        for update in ({"server": None}, {"pool": None}, {"mail": None}):
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

    def test_admin_generate_ui_polls_task_and_renders_chinese_result_status(self):
        html = server.ADMIN_HTML

        self.assertIn('id="g-submit"', html)
        self.assertIn('id="g-result"', html)
        self.assertIn("function renderGenerateResult(", html)
        self.assertIn("async function waitForGeneratedTask(", html)
        self.assertIn("await waitForGeneratedTask(r.task_id", html)
        self.assertIn("adminLabel('taskStatus',task?.status)", html)
        self.assertIn("生成结果", html)

    def synthetic_watermarked_image_bytes(self):
        image = Image.new("RGB", (360, 640), (74, 52, 96))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 359, 560), fill=(105, 78, 130))
        font = ImageFont.load_default(size=24)
        draw.text((232, 588), "Oreate AI", fill=(245, 245, 245), font=font)
        payload = io.BytesIO()
        image.save(payload, format="JPEG", quality=95)
        return payload.getvalue()

    def seed_completed_image_task(self, asset):
        conn = server.db_conn()
        row = conn.execute("SELECT id FROM accounts ORDER BY id LIMIT 1").fetchone()
        conn.close()
        account_id = row["id"] if row else self.seed_account()
        return server.save_task(
            account_id,
            "image",
            "测试图片",
            {"kind": "image", "prompt": "测试图片"},
            {"status": "completed", "assets": [asset]},
            status="completed",
            finished_at=time.time(),
        )

    def test_watermark_removal_preserves_dimensions_and_skips_clean_images(self):
        source = self.synthetic_watermarked_image_bytes()

        processed, media_type, removed = server.watermark_free_image_bytes(source)

        self.assertTrue(removed)
        self.assertEqual(media_type, "image/jpeg")
        with Image.open(io.BytesIO(processed)) as result:
            self.assertEqual(result.size, (360, 640))

        clean_image = Image.new("RGB", (360, 640), (30, 60, 90))
        clean_payload = io.BytesIO()
        clean_image.save(clean_payload, format="PNG")
        untouched, clean_media_type, clean_removed = server.watermark_free_image_bytes(clean_payload.getvalue())

        self.assertFalse(clean_removed)
        self.assertEqual(clean_media_type, "image/png")
        self.assertEqual(untouched, clean_payload.getvalue())

    def test_watermark_removal_can_force_the_small_upstream_bottom_strip(self):
        image = Image.new("RGB", (1080, 1920), (70, 50, 100))
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=22)
        draw.text((920, 1850), "Oreate AI", fill=(245, 245, 245), font=font)
        payload = io.BytesIO()
        image.save(payload, format="PNG")

        processed, media_type, removed = server.watermark_free_image_bytes(
            payload.getvalue(),
            force_bottom_strip=True,
        )

        self.assertTrue(removed)
        self.assertEqual(media_type, "image/png")
        with Image.open(io.BytesIO(processed)) as result:
            self.assertEqual(result.size, (1080, 1920))
            bottom_right = result.crop((900, 1810, 1080, 1920))
            extrema = bottom_right.convert("L").getextrema()
            self.assertLess(extrema[1] - extrema[0], 20)

    def test_admin_clean_asset_requires_login_and_rejects_untrusted_hosts(self):
        trusted_task_id = self.seed_completed_image_task("https://cdn.oreateai.com/static/result/test.jpg")
        untrusted_task_id = self.seed_completed_image_task("https://example.com/result/test.jpg")

        unauthenticated = self.client.get(f"/api/tasks/{trusted_task_id}/assets/0/clean")
        with patch.object(server, "fetch_remote_image_asset") as fetch:
            untrusted = self.client.get(
                f"/api/tasks/{untrusted_task_id}/assets/0/clean",
                headers=self.admin_headers(),
            )

        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(untrusted.status_code, 502)
        fetch.assert_not_called()

    def test_clean_asset_download_rejects_redirects_outside_the_allowlist(self):
        redirect = MagicMock()
        redirect.status_code = 302
        redirect.headers = {"location": "http://127.0.0.1/private"}
        redirect.url = "https://cdn.oreateai.com/static/result/test.jpg"

        with patch.object(server.requests, "get", return_value=redirect) as get:
            with self.assertRaises(server.HTTPException) as raised:
                server.fetch_remote_image_asset("https://cdn.oreateai.com/static/result/test.jpg")

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(raised.exception.detail, "image asset is unavailable")
        get.assert_called_once()
        redirect.close.assert_called_once()

    def test_clean_asset_download_uses_targeted_insecure_fallback_when_cdn_chain_is_incomplete(self):
        expected = self.synthetic_watermarked_image_bytes()
        fallback_response = MagicMock()
        fallback_response.status_code = 200
        fallback_response.headers = {
            "content-length": str(len(expected)),
            "content-type": "image/jpeg",
        }
        fallback_response.url = "https://cdn.oreateai.com/static/result/test.jpg"
        fallback_response.iter_content.return_value = [expected]
        fallback_response.raise_for_status.return_value = None

        with patch.object(
            server.requests,
            "get",
            side_effect=[server.requests.exceptions.SSLError(), fallback_response],
        ) as get:
            result = server.fetch_remote_image_asset(
                "https://cdn.oreateai.com/static/result/test.jpg"
            )

        self.assertEqual(result, expected)
        self.assertEqual(get.call_count, 2)
        self.assertTrue(get.call_args_list[0].kwargs["verify"])
        self.assertFalse(get.call_args_list[1].kwargs["verify"])

    def test_clean_asset_download_does_not_disable_tls_for_unconfigured_hosts(self):
        original_cfg = server.CFG
        server.CFG = server.deep_merge(
            original_cfg,
            {
                "openai_compat": {
                    "asset_host_allowlist": ["assets.example.test"],
                    "asset_insecure_tls_fallback_hosts": [],
                }
            },
        )
        try:
            with patch.object(
                server.requests,
                "get",
                side_effect=server.requests.exceptions.SSLError(),
            ) as get:
                with self.assertRaises(server.HTTPException) as raised:
                    server.fetch_remote_image_asset(
                        "https://assets.example.test/static/result/test.jpg"
                    )
        finally:
            server.CFG = original_cfg

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(get.call_count, 1)

    def test_admin_clean_asset_returns_processed_image_and_chinese_filename(self):
        task_id = self.seed_completed_image_task("https://cdn.oreateai.com/static/result/test.jpg")

        with patch.object(
            server,
            "fetch_remote_image_asset",
            return_value=self.synthetic_watermarked_image_bytes(),
        ):
            response = self.client.get(
                f"/api/tasks/{task_id}/assets/0/clean",
                headers=self.admin_headers(),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        self.assertEqual(response.headers["x-watermark-removed"], "true")
        self.assertIn("task-", response.headers["content-disposition"])
        with Image.open(io.BytesIO(response.content)) as result:
            self.assertEqual(result.size, (360, 640))

    def test_admin_clean_asset_rejects_video_tasks_and_invalid_asset_indexes(self):
        account_id = self.seed_account()
        task_id = server.save_task(
            account_id,
            "video",
            "测试视频",
            {"kind": "video", "prompt": "测试视频"},
            {"status": "completed", "assets": ["https://cdn.oreateai.com/video/result.mp4"]},
            status="completed",
            finished_at=time.time(),
        )
        headers = self.admin_headers()

        video_response = self.client.get(f"/api/tasks/{task_id}/assets/0/clean", headers=headers)
        index_response = self.client.get(f"/api/tasks/{task_id}/assets/1/clean", headers=headers)

        self.assertEqual(video_response.status_code, 409)
        self.assertEqual(index_response.status_code, 404)

    def test_admin_html_loads_and_downloads_authenticated_watermark_free_previews(self):
        html = server.ADMIN_HTML

        self.assertIn("async function loadCleanTaskImages(", html)
        self.assertIn("function imageBlobDataUrl(", html)
        self.assertIn("await image.decode()", html)
        self.assertIn("async function downloadCleanTaskAsset(", html)
        self.assertIn("/assets/${assetIndex}/clean", html)
        self.assertIn("正在生成无水印预览", html)
        self.assertIn("下载无水印", html)
        self.assertIn("打开上游原图", html)

    def test_admin_html_localizes_operational_enums_without_changing_filter_values(self):
        html = server.ADMIN_HTML

        self.assertIn('<option value="queued">待处理</option>', html)
        self.assertIn('<option value="running">生成中</option>', html)
        self.assertIn('<option value="completed">已完成</option>', html)
        self.assertIn('<option value="uploading">上传中</option>', html)
        self.assertIn('<option value="deleted">已删除</option>', html)
        self.assertIn('<option value="image">图片</option>', html)
        self.assertIn('<option value="video">视频</option>', html)
        self.assertIn("function adminLabel", html)
        self.assertIn("adminLabel('taskStatus',t.status)", html)
        self.assertIn("adminLabel('accountStatus',a.status)", html)
        self.assertIn("adminLabel('healthStatus',a.health_status)", html)
        self.assertIn("adminLabel('riskStatus',a.risk_status||'clean')", html)
        self.assertIn("adminLabel('apiKeyStatus',keyStatus)", html)
        self.assertIn("adminLabel('kind',u.kind)", html)
        self.assertIn("adminLabel('uploadStatus',item.status)", html)

        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required to execute the admin localization helper test")
        script = html.split("<script>", 1)[1].split("</script>", 1)[0]
        labels_start = script.index("const ADMIN_LABELS")
        labels_end = script.index("function normalizedOptionValues(", labels_start)
        localization_source = script[labels_start:labels_end].strip()
        node_program = f"""
{localization_source}
const cases = [
  ['taskStatus', 'queued', '待处理'],
  ['taskStatus', 'running', '生成中'],
  ['taskStatus', 'submitted', '已提交'],
  ['taskStatus', 'hydrating', '获取结果中'],
  ['taskStatus', 'completed', '已完成'],
  ['taskStatus', 'failed', '失败'],
  ['taskStatus', 'expired', '已过期'],
  ['taskStatus', 'cancelled', '已取消'],
  ['accountStatus', 'verified', '已验证'],
  ['healthStatus', 'healthy', '健康'],
  ['healthStatus', 'cooling', '冷却中'],
  ['healthStatus', 'low_balance', '余额不足'],
  ['riskStatus', 'clean', '正常'],
  ['apiKeyStatus', 'enabled', '启用'],
  ['clientStatus', 'active', '启用'],
  ['kind', 'image', '图片'],
  ['kind', 'video', '视频'],
  ['uploadStatus', 'completed', '已完成'],
  ['verificationStatus', 'live_verified', '在线验证'],
];
for (const [category, value, expected] of cases) {{
  const actual = adminLabel(category, value);
  if (actual !== expected) {{
    throw new Error(`${{category}}.${{value}}: expected ${{expected}}, got ${{actual}}`);
  }}
}}
if (adminLabel('taskStatus', 'future_status') !== 'future_status') {{
  throw new Error('unknown enum values must remain visible for forward compatibility');
}}
if (adminLabel('taskStatus', '') !== '-') {{
  throw new Error('blank enum values must render as a placeholder');
}}
"""
        node_test_path = Path(self.tmp.name) / "admin_localization_helper_test.js"
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
        source = (Path(server.__file__).resolve().parent / "gateway" / "admin_html.py").read_text(encoding="utf-8")
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
            {
                "server": {"admin_password": "secret-password"},
                "mail": {"api_key": "AC-secret-key"},
            },
        )
        try:
            response = self.client.get("/api/admin/settings", headers=self.admin_headers())
        finally:
            server.CFG = original_cfg

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["server"]["admin_password"], server.SECRET_PLACEHOLDER)
        self.assertEqual(payload["mail"]["api_key"], server.SECRET_PLACEHOLDER)

    def test_admin_settings_blank_secret_values_preserve_existing_keys(self):
        server.CFG = server.deep_merge(
            server.CFG,
            {
                "mail": {"api_key": "AC-existing-key"},
            },
        )
        server.save_config(server.CFG)

        response = self.client.put(
            "/api/admin/settings",
            headers=self.admin_headers(),
            json={
                "mail": {"api_key": ""},
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(server.CFG["mail"]["api_key"], "AC-existing-key")
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["mail"]["api_key"], "AC-existing-key")
        payload = response.json()["config"]
        self.assertEqual(payload["mail"]["api_key"], server.SECRET_PLACEHOLDER)

    def test_accounts_response_does_not_expose_credentials_or_session_cookies(self):
        account_id = self.seed_account()
        response = self.client.get("/api/accounts", headers=self.admin_headers())

        self.assertEqual(response.status_code, 200)
        account = response.json()["items"][0]
        self.assertNotIn("password", account)
        self.assertNotIn("ouss", account)
        self.assertNotIn("model_info_json", account)
        self.assertNotIn("video_info_json", account)
        self.assertNotIn("point_balance_json", account)
        self.assertEqual(account["email"], "user@example.com")

        credentials = self.client.get(
            f"/api/accounts/{account_id}/credentials",
            headers=self.admin_headers(),
        )
        self.assertEqual(credentials.status_code, 200)
        self.assertEqual(
            credentials.json(),
            {
                "id": account_id,
                "email": "user@example.com",
                "password": "plain-password",
            },
        )

        missing = self.client.get(
            "/api/accounts/999999/credentials",
            headers=self.admin_headers(),
        )
        self.assertEqual(missing.status_code, 404)

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
        modern = (
            "https://www.oreateai.com/home/vertical/aiImage"
            "?email=a%40outlook.com&amp;tokenID=72428279-b47a-4cbb-ad9e-14130d2c22a9"
        )
        self.assertEqual(
            server.extract_token_id_from_link(modern),
            "72428279-b47a-4cbb-ad9e-14130d2c22a9",
        )

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
            """
            UPDATE accounts
            SET model_info_json=?, video_info_json=?, rest_point=1000,
                daily_point=0, bonus_point=1000, balance_updated_at=?
            WHERE id=?
            """,
            (
                json.dumps(self.sample_image_info()),
                json.dumps(self.sample_video_info()),
                time.time(),
                account_id,
            ),
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

        original_cfg = server.CFG
        server.CFG = server.deep_merge(
            original_cfg,
            {"gateway": {"demo_generation_enabled": False}},
        )
        try:
            with patch.object(server, "CLIENT", StubClient()):
                response = self.client.post(
                    "/api/media/generate",
                    headers=self.admin_headers(),
                    json={"kind": "image", "prompt": "hello"},
                )
        finally:
            server.CFG = original_cfg

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["status"], "queued")
        self.assertNotIn("task", response.json())

    def test_admin_generate_ignores_legacy_demo_generation_flag(self):
        account_id = self.seed_account()
        conn = server.db_conn()
        conn.execute(
            """
            UPDATE accounts
            SET model_info_json=?, video_info_json=?, rest_point=1000,
                daily_point=0, bonus_point=1000, balance_updated_at=?
            WHERE id=?
            """,
            (
                json.dumps(self.sample_image_info()),
                json.dumps(self.sample_video_info()),
                time.time(),
                account_id,
            ),
        )
        conn.commit()
        conn.close()
        original_cfg = server.CFG
        server.CFG = server.deep_merge(
            original_cfg,
            {
                "server": {"host": "127.0.0.1"},
                "gateway": {
                    "demo_generation_enabled": True,
                    "enable_background_worker": False,
                },
            },
        )
        try:
            response = self.client.post(
                "/api/media/generate",
                headers=self.admin_headers(),
                json={
                    "kind": "image",
                    "prompt": "一个中国古风美女",
                    "model_name": "Google Nano Banana 2",
                    "ratio": "16:9",
                    "resolution": "4K",
                },
            )
        finally:
            server.CFG = original_cfg

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "queued")
        self.assertNotIn("task", payload)

        task = self.client.get(
            f"/api/tasks/{payload['task_id']}",
            headers=self.admin_headers(),
        )
        self.assertEqual(task.status_code, 200)
        self.assertEqual(task.json()["task"]["status"], "queued")
        self.assertEqual(task.json()["task"]["attempts"], [])
        self.assertEqual(task.json()["task"]["assets"], [])

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

    def test_registration_job_persists_progress_and_redacts_results(self):
        job = server.create_registration_job(2)
        calls = {"count": 0}

        def fake_register(count, progress=None):
            items = []
            for _ in range(max(1, int(count))):
                calls["count"] += 1
                email = f"user{calls['count']}@example.com"
                if progress:
                    progress("create_mailbox", email)
                    progress("signup_attempt", email)
                items.append(
                    {
                        "ok": calls["count"] == 1,
                        "status": "verified" if calls["count"] == 1 else "signup_failed",
                        "account_id": calls["count"] if calls["count"] == 1 else None,
                        "email": email,
                        "password": "Aa1@secret123",
                    }
                )
            return items

        with patch.object(server, "auto_register_accounts", side_effect=fake_register):
            server.run_registration_job(job["id"])

        completed = server.get_registration_job(job["id"])
        self.assertEqual(completed["status"], "completed_with_errors")
        self.assertEqual(completed["total"], 2)
        self.assertEqual(completed["completed"], 2)
        self.assertEqual(completed["succeeded"], 1)
        self.assertEqual(completed["failed"], 1)
        self.assertEqual(len(completed["items"]), 2)
        self.assertNotIn("password", json.dumps(completed["items"], ensure_ascii=False))
        self.assertEqual(completed["current_step"], "completed")
        events = completed.get("events") or []
        self.assertGreaterEqual(len(events), 2)
        self.assertTrue(any(event.get("step") == "create_mailbox" for event in events))
        self.assertTrue(
            any(
                event.get("level") == "success" and event.get("email") == "user1@example.com"
                for event in events
            )
        )
        self.assertTrue(
            any(
                event.get("level") == "error" and event.get("email") == "user2@example.com"
                for event in events
            )
        )
        self.assertNotIn("Aa1@secret123", json.dumps(events, ensure_ascii=False))

    def test_registration_job_api_returns_immediately_and_exposes_progress(self):
        with patch.object(server, "launch_registration_job") as launch:
            created = self.client.post(
                "/api/register/jobs",
                headers=self.admin_headers(),
                json={"count": 3},
            )

        self.assertEqual(created.status_code, 202)
        job = created.json()["job"]
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["total"], 3)
        launch.assert_called_once_with(job["id"])

        detail = self.client.get(
            f"/api/register/jobs/{job['id']}",
            headers=self.admin_headers(),
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["job"]["id"], job["id"])

    def test_registration_worker_marks_unexpected_failure_terminal(self):
        job = server.create_registration_job(1)
        with patch.object(
            server,
            "run_registration_job",
            side_effect=RuntimeError("unexpected registration worker failure"),
        ):
            server.launch_registration_job(job["id"])
            deadline = time.time() + 2
            while time.time() < deadline:
                with server.REGISTRATION_THREADS_LOCK:
                    running = job["id"] in server.REGISTRATION_THREADS
                if not running:
                    break
                time.sleep(0.01)

        failed = server.get_registration_job(job["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["current_step"], "failed")
        self.assertIn("unexpected registration worker failure", failed["error_message"])

    def test_pool_maintenance_isolates_risk_accounts_and_supplements_healthy_deficit(self):
        now = time.time()
        image_info = json.dumps(self.sample_image_info(), ensure_ascii=False)
        conn = server.db_conn()
        conn.executemany(
            """
            INSERT INTO accounts(
                email,password,status,source,ouid,ouss,model_info_json,video_info_json,
                last_error,rest_point,balance_updated_at,created_at,updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    "healthy@example.com",
                    "password",
                    "verified",
                    "manual",
                    "ouid-healthy",
                    "ouss-healthy",
                    image_info,
                    "{}",
                    None,
                    100,
                    now,
                    now,
                    now,
                ),
                (
                    "risk@example.com",
                    "password",
                    "verified",
                    "manual",
                    "ouid-risk",
                    "ouss-risk",
                    image_info,
                    "{}",
                    "212361: account risk controlled",
                    100,
                    now,
                    now,
                    now,
                ),
                (
                    "invalid@example.com",
                    "password",
                    "invalid",
                    "manual",
                    "ouid-invalid",
                    "ouss-invalid",
                    image_info,
                    "{}",
                    "200001: session expired",
                    100,
                    now,
                    now,
                    now,
                ),
            ],
        )
        conn.commit()
        conn.close()

        job = server.create_pool_maintenance_job(
            clean_risk=True,
            supplement=True,
            target_healthy=3,
            max_register=2,
        )
        registration_calls = {"count": 0}

        def fake_register(count, progress=None):
            self.assertEqual(count, 1)
            registration_calls["count"] += 1
            email = f"supplement-{registration_calls['count']}@example.com"
            if progress:
                progress("create_mailbox", email)
            server.save_account(
                email,
                "password",
                server.OreateSession(
                    email=email,
                    password="password",
                    cookies={
                        "OUID": f"ouid-{registration_calls['count']}",
                        "ouss": f"ouss-{registration_calls['count']}",
                    },
                ),
                model_info=self.sample_image_info(),
                video_info={},
                status="verified",
                source="auto",
            )
            return [{"ok": True, "status": "verified", "email": email, "password": "secret"}]

        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "fetch_account_point_detail", return_value={"restPoint": 100}),
            patch.object(
                server,
                "probe_account_generation_health",
                return_value={"ok": True, "asset_count": 1},
            ),
            patch.object(server, "auto_register_accounts", side_effect=fake_register),
        ):
            server.run_pool_maintenance_job(job["id"])

        completed = server.get_pool_maintenance_job(job["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["healthy_before"], 2)
        self.assertEqual(completed["risk_found"], 0)
        self.assertEqual(completed["invalid_found"], 1)
        self.assertEqual(completed["isolated_accounts"], 1)
        self.assertEqual(completed["registration_target"], 1)
        self.assertEqual(completed["registered"], 1)
        self.assertEqual(completed["healthy_after"], 3)
        self.assertNotIn("secret", json.dumps(completed["items"], ensure_ascii=False))

        conn = server.db_conn()
        isolated = {
            row["email"]: row["status"]
            for row in conn.execute(
                "SELECT email,status FROM accounts WHERE email IN (?,?)",
                ("risk@example.com", "invalid@example.com"),
            ).fetchall()
        }
        conn.close()
        self.assertEqual(isolated["risk@example.com"], "verified")
        self.assertEqual(isolated["invalid@example.com"], "disabled")

    def test_pool_maintenance_stops_on_gateway_risk_without_isolating_account(self):
        account_id = self.seed_account()
        conn = server.db_conn()
        conn.execute(
            "UPDATE accounts SET model_info_json=?, rest_point=? WHERE id=?",
            (json.dumps(self.sample_image_info()), 100, account_id),
        )
        conn.commit()
        conn.close()
        job = server.create_pool_maintenance_job(
            clean_risk=True,
            supplement=False,
            target_healthy=1,
            max_register=0,
        )

        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(
                server.CLIENT,
                "fetch_account_point_detail",
                side_effect=RuntimeError("getpointdetail failed: status code 212361"),
            ),
        ):
            server.run_pool_maintenance_job(job["id"])

        completed = server.get_pool_maintenance_job(job["id"])
        self.assertEqual(completed["status"], "completed_with_errors")
        self.assertEqual(completed["risk_found"], 0)
        self.assertEqual(completed["isolated_accounts"], 0)
        self.assertEqual(completed["current_step"], "gateway_risk")
        self.assertIn("生成环境", completed["error_message"])
        self.assertIn("gateway_risk:'生成环境异常，已停止检测'", server.ADMIN_HTML)
        self.assertIn("gateway_risk:'生成环境异常'", server.ADMIN_HTML)
        self.assertIn("aborted:'已停止检测'", server.ADMIN_HTML)
        conn = server.db_conn()
        row = conn.execute("SELECT id,status,last_error FROM accounts WHERE id=?", (account_id,)).fetchone()
        conn.close()
        self.assertEqual(row["id"], account_id)
        self.assertEqual(row["status"], "verified")
        self.assertIn("212361", row["last_error"])

    def test_pool_maintenance_does_not_treat_generation_environment_as_account_risk(self):
        account_id = self.seed_account()
        conn = server.db_conn()
        conn.execute(
            "UPDATE accounts SET model_info_json=?, rest_point=? WHERE id=?",
            (json.dumps(self.sample_image_info()), 100, account_id),
        )
        conn.commit()
        conn.close()
        job = server.create_pool_maintenance_job(
            clean_risk=True,
            supplement=False,
            target_healthy=1,
            max_register=0,
        )

        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(
                server.CLIENT,
                "fetch_account_point_detail",
                return_value={"restPoint": 100},
            ),
            patch.object(
                server,
                "submit_generation_for_account",
                side_effect=server.UpstreamGenerationError(
                    {"code": "212361", "message": "spam user"}
                ),
            ) as submit_probe,
        ):
            server.run_pool_maintenance_job(job["id"])

        completed = server.get_pool_maintenance_job(job["id"])
        self.assertEqual(completed["status"], "completed_with_errors")
        self.assertEqual(completed["risk_found"], 0)
        self.assertEqual(completed["isolated_accounts"], 0)
        self.assertEqual(completed["current_step"], "gateway_risk")
        submit_probe.assert_called_once()

        conn = server.db_conn()
        row = conn.execute(
            "SELECT status,last_error FROM accounts WHERE id=?",
            (account_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(row["status"], "verified")
        self.assertIn("212361", row["last_error"])

    def test_startup_recovery_restores_accounts_misclassified_by_gateway_risk(self):
        restored_id = self.seed_account()
        conn = server.db_conn()
        now = time.time()
        conn.execute(
            """
            INSERT INTO accounts(
                email,password,status,source,ouid,ouss,model_info_json,video_info_json,
                created_at,updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "invalid@example.com",
                "plain-password",
                "verified",
                "manual",
                "ouid-invalid",
                "ouss-invalid",
                "{}",
                "{}",
                now,
                now,
            ),
        )
        invalid_id = int(conn.execute(
            "SELECT id FROM accounts WHERE email='invalid@example.com'"
        ).fetchone()[0])
        conn.execute(
            """
            UPDATE accounts
            SET status='disabled', failure_count=4, cooldown_until=?,
                last_error='212361: spam user', model_info_json=?
            WHERE id=?
            """,
            (time.time() + 3600, json.dumps(self.sample_image_info()), restored_id),
        )
        conn.execute(
            """
            UPDATE accounts
            SET status='disabled', failure_count=2,
                last_error='200001: session expired', model_info_json=?
            WHERE id=?
            """,
            (json.dumps(self.sample_image_info()), invalid_id),
        )
        conn.commit()
        conn.close()

        restored = server.restore_gateway_risk_misclassified_accounts()

        self.assertEqual(restored, 1)
        conn = server.db_conn()
        restored_row = conn.execute(
            "SELECT status,failure_count,cooldown_until,last_error FROM accounts WHERE id=?",
            (restored_id,),
        ).fetchone()
        invalid_row = conn.execute(
            "SELECT status,last_error FROM accounts WHERE id=?",
            (invalid_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(restored_row["status"], "verified")
        self.assertEqual(restored_row["failure_count"], 0)
        self.assertIsNone(restored_row["cooldown_until"])
        self.assertIsNone(restored_row["last_error"])
        self.assertEqual(invalid_row["status"], "disabled")
        self.assertIn("200001", invalid_row["last_error"])

    def test_pool_maintenance_revalidates_and_promotes_pending_registered_account(self):
        account_id = self.seed_account()
        conn = server.db_conn()
        conn.execute(
            """
            UPDATE accounts
            SET status='pending_validation',
                model_info_json=?,
                rest_point=NULL,
                balance_updated_at=NULL,
                last_used_at=NULL
            WHERE id=?
            """,
            (json.dumps(self.sample_image_info()), account_id),
        )
        conn.commit()
        conn.close()
        job = server.create_pool_maintenance_job(
            clean_risk=True,
            supplement=False,
            target_healthy=1,
            max_register=0,
        )

        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(
                server.CLIENT,
                "fetch_account_point_detail",
                return_value={"restPoint": 100},
            ),
            patch.object(
                server,
                "probe_account_generation_health",
                return_value={"ok": True, "asset_count": 1},
            ),
        ):
            server.run_pool_maintenance_job(job["id"])

        completed = server.get_pool_maintenance_job(job["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["healthy_before"], 0)
        self.assertEqual(completed["healthy_after"], 1)
        conn = server.db_conn()
        row = conn.execute(
            "SELECT status,last_error FROM accounts WHERE id=?",
            (account_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(row["status"], "verified")
        self.assertIsNone(row["last_error"])

    def test_pool_maintenance_does_not_reprocess_previously_isolated_accounts(self):
        account_id = self.seed_account()
        conn = server.db_conn()
        conn.execute(
            """
            UPDATE accounts
            SET status='disabled',
                last_error='212361: previously isolated',
                model_info_json=?,
                rest_point=?
            WHERE id=?
            """,
            (json.dumps(self.sample_image_info()), 100, account_id),
        )
        conn.commit()
        conn.close()
        job = server.create_pool_maintenance_job(
            clean_risk=True,
            supplement=False,
            target_healthy=1,
            max_register=0,
        )

        with (
            patch.object(server.CLIENT, "session_from_account") as session_from_account,
            patch.object(server.CLIENT, "fetch_account_point_detail") as fetch_point_detail,
        ):
            server.run_pool_maintenance_job(job["id"])

        completed = server.get_pool_maintenance_job(job["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["checked_accounts"], 1)
        self.assertEqual(completed["risk_found"], 0)
        self.assertEqual(completed["isolated_accounts"], 0)
        self.assertEqual(completed["items"], [])
        session_from_account.assert_not_called()
        fetch_point_detail.assert_not_called()

    def test_refresh_account_session_replaces_cookies_and_revalidates_account(self):
        account_id = self.seed_account()
        conn = server.db_conn()
        conn.execute(
            """
            UPDATE accounts
            SET status='disabled',
                source='manual',
                last_error='200001: session expired'
            WHERE id=?
            """,
            (account_id,),
        )
        conn.commit()
        conn.close()
        refreshed_session = server.OreateSession(
            email="test@example.com",
            password="password",
            cookies={"OUID": "fresh-ouid", "ouss": "fresh-ouss"},
        )

        with (
            patch.object(server.CLIENT, "login", return_value=refreshed_session) as login,
            patch.object(server.CLIENT, "session_from_cookie_dict", return_value=object()),
            patch.object(
                server.CLIENT,
                "fetch_image_models",
                return_value=self.sample_image_info(),
            ),
            patch.object(server.CLIENT, "fetch_video_models", return_value={"data": []}),
            patch.object(server.CLIENT, "fetch_video_scenes", return_value={"data": []}),
            patch.object(
                server,
                "validate_registered_account",
                return_value={"ok": True, "asset_count": 1},
            ) as validate,
        ):
            result = server.refresh_account_session_and_validate(account_id)

        self.assertTrue(result["ok"])
        login.assert_called_once_with("user@example.com", "plain-password")
        validate.assert_called_once_with(account_id)
        conn = server.db_conn()
        row = conn.execute(
            "SELECT status,source,ouid,ouss FROM accounts WHERE id=?",
            (account_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(row["status"], "pending_validation")
        self.assertEqual(row["source"], "manual")
        self.assertEqual(server.decrypt_secret_value(row["ouid"]), "fresh-ouid")
        self.assertEqual(server.decrypt_secret_value(row["ouss"]), "fresh-ouss")

    def test_refresh_account_session_keeps_transient_login_failure_retriable(self):
        account_id = self.seed_account()
        conn = server.db_conn()
        conn.execute(
            """
            UPDATE accounts
            SET status='disabled',
                last_error='200001: session expired'
            WHERE id=?
            """,
            (account_id,),
        )
        conn.commit()
        conn.close()

        with patch.object(
            server.CLIENT,
            "login",
            side_effect=RuntimeError(
                "emaillogin failed: {'status': {'code': 100003}}"
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "100003"):
                server.refresh_account_session_and_validate(account_id)

        conn = server.db_conn()
        row = conn.execute(
            "SELECT status,last_error FROM accounts WHERE id=?",
            (account_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(row["status"], "pending_validation")
        self.assertIn("200001", row["last_error"])

    def test_pool_maintenance_recovers_previously_isolated_expired_session(self):
        account_id = self.seed_account()
        conn = server.db_conn()
        conn.execute(
            """
            UPDATE accounts
            SET status='disabled',
                last_error='200001: session expired',
                model_info_json=?,
                rest_point=?
            WHERE id=?
            """,
            (json.dumps(self.sample_image_info()), 100, account_id),
        )
        conn.commit()
        conn.close()
        job = server.create_pool_maintenance_job(
            clean_risk=True,
            supplement=False,
            target_healthy=1,
            max_register=0,
        )

        def recover(expired_account_id):
            self.assertEqual(expired_account_id, account_id)
            conn = server.db_conn()
            conn.execute(
                """
                UPDATE accounts
                SET status='verified',
                    last_error=NULL,
                    cooldown_until=NULL
                WHERE id=?
                """,
                (expired_account_id,),
            )
            conn.commit()
            conn.close()
            return {"ok": True, "asset_count": 1}

        with patch.object(
            server,
            "refresh_account_session_and_validate",
            side_effect=recover,
        ) as refresh:
            server.run_pool_maintenance_job(job["id"])

        completed = server.get_pool_maintenance_job(job["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["healthy_after"], 1)
        self.assertEqual(completed["invalid_found"], 0)
        self.assertEqual(completed["isolated_accounts"], 0)
        self.assertEqual(completed["items"], [])
        refresh.assert_called_once_with(account_id)

    def test_pool_maintenance_refreshes_live_expired_session_before_isolating(self):
        account_id = self.seed_account()
        conn = server.db_conn()
        conn.execute(
            "UPDATE accounts SET model_info_json=?, rest_point=? WHERE id=?",
            (json.dumps(self.sample_image_info()), 100, account_id),
        )
        conn.commit()
        conn.close()
        job = server.create_pool_maintenance_job(
            clean_risk=True,
            supplement=False,
            target_healthy=1,
            max_register=0,
        )

        def recover(expired_account_id):
            conn = server.db_conn()
            conn.execute(
                "UPDATE accounts SET status='verified', last_error=NULL WHERE id=?",
                (expired_account_id,),
            )
            conn.commit()
            conn.close()
            return {"ok": True, "asset_count": 1}

        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(
                server.CLIENT,
                "fetch_account_point_detail",
                side_effect=RuntimeError(
                    "getpointdetail failed: {'status': {'code': 200001}}"
                ),
            ),
            patch.object(
                server,
                "refresh_account_session_and_validate",
                side_effect=recover,
            ) as refresh,
        ):
            server.run_pool_maintenance_job(job["id"])

        completed = server.get_pool_maintenance_job(job["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["invalid_found"], 0)
        self.assertEqual(completed["isolated_accounts"], 0)
        refresh.assert_called_once_with(account_id)

    def test_pool_maintenance_job_api_returns_immediately_and_exposes_chinese_progress(self):
        with patch.object(server, "launch_pool_maintenance_job") as launch:
            created = self.client.post(
                "/api/pool/maintenance/jobs",
                headers=self.admin_headers(),
                json={
                    "clean_risk": True,
                    "supplement": True,
                    "target_healthy": 5,
                    "max_register": 3,
                },
            )

        self.assertEqual(created.status_code, 202)
        job = created.json()["job"]
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["current_step"], "queued")
        launch.assert_called_once_with(job["id"])

        duplicate = self.client.post(
            "/api/pool/maintenance/jobs",
            headers=self.admin_headers(),
            json={
                "clean_risk": True,
                "supplement": True,
                "target_healthy": 5,
                "max_register": 3,
            },
        )
        self.assertEqual(duplicate.status_code, 409)
        self.assertIn("正在执行", duplicate.json()["detail"])

        detail = self.client.get(
            f"/api/pool/maintenance/jobs/{job['id']}",
            headers=self.admin_headers(),
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["job"]["target_healthy"], 5)

    def test_admin_html_sends_bearer_token_for_api_calls(self):
        html = server.ADMIN_HTML
        self.assertIn("localStorage", html)
        self.assertIn("Authorization", html)
        self.assertIn("/api/admin/login", html)
        # Multipart uploads must not force Content-Type: application/json.
        self.assertIn("authHeaders({multipart:true})", html)
        self.assertIn("if(!options.multipart) headers['Content-Type']='application/json';", html)

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
        self.assertIn("API 调用文档", html)
        self.assertIn('id="tab-docs"', html)
        self.assertIn('id="registration-progress"', html)
        self.assertIn("/api/register/jobs", html)
        self.assertIn("/credentials", html)
        self.assertIn("查看密码", html)
        self.assertIn("复制密码", html)
        self.assertIn("连接暂时中断，正在重试", html)
        self.assertIn("体检并补号", html)
        self.assertIn("清理僵尸号", html)
        self.assertIn("/api/accounts/purge-zombies", html)
        self.assertIn("/api/pool/maintenance/jobs", html)
        self.assertIn('id="maintenance-progress"', html)
        self.assertIn('id="tab-outlook"', html)
        self.assertIn("/api/mail/outlook/import-file", html)
        self.assertIn("loadOutlookMailboxes", html)
        self.assertNotIn("s-admin-pwd", html)

    def test_admin_html_escapes_untrusted_usage_account_and_client_values(self):
        html = server.ADMIN_HTML

        self.assertIn("${escapeHtml((u.prompt||'').substring(0,40))}", html)
        self.assertIn("${escapeHtml(u.account_email||u.account_id||'-')}", html)
        self.assertIn("${escapeHtml(u.model_name||'-')}", html)
        self.assertIn("${escapeHtml(em)}", html)
        self.assertIn("data-copy-value=\"${escapeHtml(em)}\"", html)
        self.assertIn("${escapeHtml(apiKeyDisplayName(k))}", html)
        self.assertIn("${escapeHtml(customerName)}", html)
        self.assertIn("return `${escapeHtml(kinds)}", html)
        self.assertIn("${escapeHtml(models)}", html)
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
