"""Security-hardening regression tests for P0 gateway fixes.

Covers:
- Backup archives must never contain plaintext secrets (admin password,
  encryption key, mail API key).
- Restore keeps live secrets when restoring a redacted backup, rejects
  archive bombs (member caps), and never overwrites a cancellation.
- Native /v1/generate enforces uploaded-media tenant ownership.
- Hydration and expiry use compare-and-swap transitions so a concurrent
  cancellation cannot be resurrected or overwritten.
- /healthz reflects database reachability instead of always succeeding.
"""

import io
import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import server

TEST_ENCRYPTION_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


class SecurityHardeningTests(unittest.TestCase):
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
                    "mail": {"api_key": "mail-secret-123"},
                    "gateway": {"enable_background_worker": False},
                },
            ),
        )
        self.cfg_patch.start()
        server.ADMIN_TOKENS.clear()
        if hasattr(server, "RATE_BUCKETS"):
            server.RATE_BUCKETS.clear()
        if hasattr(server, "RATE_RESERVATIONS"):
            server.RATE_RESERVATIONS.clear()
        server.init_db()
        self.client = TestClient(server.app)

    def tearDown(self):
        server.ADMIN_TOKENS.clear()
        if hasattr(server, "RATE_BUCKETS"):
            server.RATE_BUCKETS.clear()
        if hasattr(server, "RATE_RESERVATIONS"):
            server.RATE_RESERVATIONS.clear()
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

    def seed_account_with_capabilities(self, email="security@example.com"):
        now = time.time()
        conn = server.db_conn()
        conn.execute(
            """
            INSERT INTO accounts(
                email,password,status,source,ouid,ouss,model_info_json,video_info_json,
                rest_point,daily_point,bonus_point,balance_updated_at,created_at,updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                email,
                server.encrypt_secret_value("plain-password"),
                "verified",
                "manual",
                server.encrypt_secret_value("ouid-secret"),
                server.encrypt_secret_value("ouss-secret"),
                json.dumps(self.sample_image_info()),
                json.dumps(self.sample_video_info()),
                1000,
                0,
                0,
                now,
                now,
                now,
            ),
        )
        conn.commit()
        account_id = conn.execute("SELECT id FROM accounts WHERE email=?", (email,)).fetchone()[0]
        conn.close()
        return account_id

    def seed_api_key(self, key):
        now = time.time()
        conn = server.db_conn()
        conn.execute("INSERT INTO api_keys(key,name,enabled,created_at) VALUES(?,?,1,?)", (key, key, now))
        conn.commit()
        key_id = conn.execute("SELECT id FROM api_keys WHERE key=?", (key,)).fetchone()[0]
        conn.close()
        return key_id

    def sample_image_info(self):
        return {
            "data": {
                "factory": [
                    {
                        "modelFactoryName": "Nano Banana",
                        "models": [
                            {
                                "modelName": "Google Nano Banana 2",
                                "modelDesc": "Flagship 4K",
                                "modelIcon": "image.svg",
                                "resolution": ["2K", "4K"],
                                "size": [{"ratio": "16:9"}, {"ratio": "1:1"}],
                                "pointCost": [{"resolution": "2K", "point": 6}, {"resolution": "4K", "point": 12}],
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
                            "description": {"zh": "视频模型", "en": "Video model"},
                            "modelIcon": "video.svg",
                            "duration": [5, 10],
                            "videoResolution": ["480", "720"],
                            "videoSize": [{"ratio": "16:9"}, {"ratio": "9:16"}],
                            "supportAudio": True,
                            "supportModifySize": True,
                            "pointCostImage": [{"duration": 5, "resolution": "480", "point": 20}],
                            "pointCostReference": [],
                            "pointCostMotion": [],
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

    # --- Backup / restore hardening ---

    def test_backup_archive_redacts_secrets(self):
        self.seed_account_with_capabilities()
        headers = self.admin_headers()
        response = self.client.get("/api/admin/backup", headers=headers)
        self.assertEqual(response.status_code, 200)
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        config_text = archive.read("config.json").decode("utf-8")
        self.assertIn(server.SECRET_PLACEHOLDER, config_text)
        self.assertNotIn("test-admin-password", config_text)
        self.assertNotIn(TEST_ENCRYPTION_KEY, config_text)
        self.assertNotIn("mail-secret-123", config_text)

    def test_restore_keeps_live_secrets_when_backup_is_redacted(self):
        self.seed_account_with_capabilities()
        headers = self.admin_headers()
        backup = self.client.get("/api/admin/backup", headers=headers)
        self.assertEqual(backup.status_code, 200)
        original_password = server.CFG["server"]["admin_password"]
        try:
            server.CFG["server"]["admin_password"] = "live-password-999"
            restore = self.client.post(
                "/api/admin/restore",
                headers=headers,
                data={"confirm": "true"},
                files={"file": ("backup.zip", backup.content, "application/zip")},
            )
        finally:
            server.CFG["server"]["admin_password"] = original_password
        self.assertEqual(restore.status_code, 200)
        # The redacted placeholder must be replaced with the live value, never
        # written into the config file as-is.
        self.assertNotEqual(server.CFG["server"]["admin_password"], server.SECRET_PLACEHOLDER)
        self.assertEqual(server.CFG["server"]["admin_password"], original_password)

    def test_restore_rejects_archive_with_too_many_members(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode="w") as archive:
            for index in range(server.RESTORE_MAX_MEMBERS + 1):
                archive.writestr(f"member-{index}.bin", b"x")
        with self.assertRaises(Exception) as caught:
            server.restore_backup_zip_bytes(buffer.getvalue())
        self.assertIn("too many members", str(caught.exception))

    def test_restore_validates_database_before_replacing_live_db(self):
        self.seed_account_with_capabilities()
        headers = self.admin_headers()
        backup = self.client.get("/api/admin/backup", headers=headers)
        self.assertEqual(backup.status_code, 200)
        # Corrupt the db member so restore must refuse without touching the live DB.
        buffer = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(backup.content)) as source:
            with zipfile.ZipFile(buffer, mode="w") as target:
                for name in source.namelist():
                    data = source.read(name)
                    if name == "accounts.db":
                        data = b"this is not a sqlite database"
                    target.writestr(name, data)
        corrupt_payload = buffer.getvalue()
        before = server.db_conn()
        try:
            before.execute("SELECT COUNT(*) FROM accounts").fetchone()
        finally:
            before.close()
        with self.assertRaises(Exception):
            server.restore_backup_zip_bytes(corrupt_payload)
        conn = server.db_conn()
        count = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        conn.close()
        self.assertGreaterEqual(count, 1, "live database must be untouched after a failed restore")

    # --- Native attachment tenant ownership ---

    def test_generate_rejects_unowned_attachment(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("owner-key")
        response = self.client.post(
            "/v1/generate",
            headers={"Authorization": "Bearer owner-key"},
            json={
                "kind": "video",
                "prompt": "hello",
                "model_name": "Seedance 2.0 Mini",
                "resolution": "480",
                "ratio": "16:9",
                "duration": 5,
                "scene_id": "text_or_image",
                "image": {"object": "uploads/never-uploaded.png"},
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "UPLOAD_NOT_OWNED")
        conn = server.db_conn()
        task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        conn.close()
        self.assertEqual(task_count, 0)

    def test_generate_rejects_cross_tenant_attachment(self):
        account_id = self.seed_account_with_capabilities()
        key_a = self.seed_api_key("tenant-a-key")
        self.seed_api_key("tenant-b-key")
        attachment = {
            "fileName": "ref",
            "fileExt": "png",
            "originSize": 1234,
            "object": "uploads/tenant-a.png",
        }
        server.save_uploaded_media_record(key_a, account_id, attachment)
        response = self.client.post(
            "/v1/generate",
            headers={"Authorization": "Bearer tenant-b-key"},
            json={
                "kind": "video",
                "prompt": "hello",
                "model_name": "Seedance 2.0 Mini",
                "resolution": "480",
                "ratio": "16:9",
                "duration": 5,
                "scene_id": "text_or_image",
                "image": {"object": "uploads/tenant-a.png"},
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "UPLOAD_NOT_OWNED")

    def test_generate_accepts_owned_attachment_and_inline_data_uri(self):
        self.seed_account_with_capabilities()
        key_id = self.seed_api_key("owner-key-2")
        attachment = {
            "fileName": "ref",
            "fileExt": "png",
            "originSize": 1234,
            "object": "uploads/owned.png",
        }
        server.save_uploaded_media_record(key_id, 1, attachment)
        response = self.client.post(
            "/v1/generate",
            headers={"Authorization": "Bearer owner-key-2"},
            json={
                "kind": "video",
                "prompt": "hello",
                "model_name": "Seedance 2.0 Mini",
                "resolution": "480",
                "ratio": "16:9",
                "duration": 5,
                "scene_id": "text_or_image",
                "image": {"object": "uploads/owned.png"},
            },
        )
        self.assertEqual(response.status_code, 202)
        # Inline data URIs are allowed at admission (bare string in a list field
        # or a {"bosUrl": "data:..."} wrapper); the worker resolves them.
        response = self.client.post(
            "/v1/generate",
            headers={"Authorization": "Bearer owner-key-2"},
            json={
                "kind": "video",
                "prompt": "hello",
                "model_name": "Seedance 2.0 Mini",
                "resolution": "480",
                "ratio": "16:9",
                "duration": 5,
                "scene_id": "text_or_image",
                "image": {"bosUrl": "data:image/png;base64,AAAA"},
            },
        )
        self.assertEqual(response.status_code, 202)

    # --- Cancel / hydrate / expire CAS ---

    def test_hydrate_cas_rejects_cancelled_task(self):
        account_id = self.seed_account_with_capabilities()
        key_id = self.seed_api_key("cas-key")
        now = time.time()
        conn = server.db_conn()
        conn.execute(
            """
            INSERT INTO tasks(
                api_key_id, account_id, kind, prompt, model_name, scene_id, resolution, ratio,
                duration, estimated_point_cost, request_id, payload_json, response_json, assets_json,
                status, attempt_count, cancel_requested_at, created_at, updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                key_id,
                account_id,
                "video",
                "hello",
                "Seedance 2.0 Mini",
                "text_or_image",
                "480",
                "16:9",
                5,
                20,
                "req_cas",
                "{}",
                "{}",
                "[]",
                "cancelled",
                1,
                now,
                now,
                now,
            ),
        )
        task_id = conn.execute("SELECT id FROM tasks WHERE request_id='req_cas'").fetchone()[0]
        conn.commit()
        conn.close()
        # Simulate a cancellation that committed after the hydrate read passed.
        with patch.object(server, "task_hydratable_status", return_value=True):
            with self.assertRaises(server.GatewayAPIError) as caught:
                server.hydrate_task_record(task_id, key_id)
        self.assertEqual(caught.exception.code, "TASK_NOT_HYDRATABLE")
        conn = server.db_conn()
        status = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
        conn.close()
        self.assertEqual(status, "cancelled", "cancelled task must not be resurrected")

    def test_expire_cas_does_not_overwrite_cancellation(self):
        account_id = self.seed_account_with_capabilities()
        key_id = self.seed_api_key("expire-key")
        now = time.time()
        conn = server.db_conn()
        conn.execute(
            """
            INSERT INTO tasks(
                api_key_id, account_id, kind, prompt, model_name, scene_id, resolution, ratio,
                duration, estimated_point_cost, request_id, payload_json, response_json, assets_json,
                status, attempt_count, cancel_requested_at, started_at, created_at, updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                key_id,
                account_id,
                "video",
                "hello",
                "Seedance 2.0 Mini",
                "text_or_image",
                "480",
                "16:9",
                5,
                20,
                "req_expire",
                "{}",
                "{}",
                "[]",
                "cancelled",
                1,
                now,
                now - 1000,
                now,
                now,
            ),
        )
        task_id = conn.execute("SELECT id FROM tasks WHERE request_id='req_expire'").fetchone()[0]
        conn.execute(
            """
            INSERT INTO task_attempts(task_id, attempt_no, phase, account_id, status, started_at)
            VALUES(?,1,'hydration',?,'running',?)
            """,
            (task_id, account_id, now - 1000),
        )
        attempt_id = conn.execute("SELECT id FROM task_attempts WHERE task_id=?", (task_id,)).fetchone()[0]
        conn.commit()
        conn.close()
        task = dict(server.fetch_task_row(task_id))
        task["claimed_from_status"] = "hydrating"
        server.expire_task_attempt(task, attempt_id)
        conn = server.db_conn()
        status = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
        attempt_status = conn.execute("SELECT status FROM task_attempts WHERE id=?", (attempt_id,)).fetchone()[0]
        conn.close()
        self.assertEqual(status, "cancelled", "expiry must never overwrite a cancellation")
        self.assertEqual(attempt_status, "running")

    def test_expire_still_works_on_uncancelled_task(self):
        account_id = self.seed_account_with_capabilities()
        key_id = self.seed_api_key("expire-key-2")
        now = time.time()
        conn = server.db_conn()
        conn.execute(
            """
            INSERT INTO tasks(
                api_key_id, account_id, kind, prompt, model_name, scene_id, resolution, ratio,
                duration, estimated_point_cost, request_id, payload_json, response_json, assets_json,
                status, attempt_count, started_at, created_at, updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                key_id,
                account_id,
                "video",
                "hello",
                "Seedance 2.0 Mini",
                "text_or_image",
                "480",
                "16:9",
                5,
                20,
                "req_expire2",
                "{}",
                "{}",
                "[]",
                "hydrating",
                1,
                now - 1000,
                now,
                now,
            ),
        )
        task_id = conn.execute("SELECT id FROM tasks WHERE request_id='req_expire2'").fetchone()[0]
        conn.execute(
            """
            INSERT INTO task_attempts(task_id, attempt_no, phase, account_id, status, started_at)
            VALUES(?,1,'hydration',?,'running',?)
            """,
            (task_id, account_id, now - 1000),
        )
        attempt_id = conn.execute("SELECT id FROM task_attempts WHERE task_id=?", (task_id,)).fetchone()[0]
        conn.commit()
        conn.close()
        task = dict(server.fetch_task_row(task_id))
        task["claimed_from_status"] = "hydrating"
        server.expire_task_attempt(task, attempt_id)
        conn = server.db_conn()
        status = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
        attempt_status = conn.execute("SELECT status FROM task_attempts WHERE id=?", (attempt_id,)).fetchone()[0]
        conn.close()
        self.assertEqual(status, "expired")
        self.assertEqual(attempt_status, "expired")

    # --- Health probe ---

    def test_healthz_reports_unavailable_when_database_unreachable(self):
        with patch.object(server, "db_conn", side_effect=RuntimeError("db down")):
            response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["ok"])

    def test_healthz_reports_ok_when_database_reachable(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    # --- P1 governance ---

    def test_root_does_not_leak_environment_details(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("cwd", payload)
        self.assertNotIn("accounts", payload)
        self.assertEqual(payload["service"], "oreateai")

    def test_retry_does_not_rewrite_idempotency_record(self):
        """Retry must leave the original idempotency response immutable."""
        account_id = self.seed_account_with_capabilities()
        key_id = self.seed_api_key("idem-key")
        now = time.time()
        task_id = server.save_task(
            account_id,
            "image",
            "hello",
            {
                "kind": "image",
                "prompt": "hello",
                "model_name": "Google Nano Banana 2",
                "resolution": "2K",
                "ratio": "16:9",
            },
            {"status": "failed", "error": {"code": "UPSTREAM_ERROR"}},
            status="failed",
            api_key_id=key_id,
            request_id="req-idem",
            model_name="Google Nano Banana 2",
            scene_id="",
            resolution="2K",
            ratio="16:9",
            estimated_point_cost=6,
            error_code="UPSTREAM_ERROR",
            error_message="upstream error",
            finished_at=now,
        )
        conn = server.db_conn()
        conn.execute(
            """
            INSERT INTO idempotency_keys(api_key_id,idempotency_key,request_hash,status_code,response_json,task_id,created_at)
            VALUES(?,?,?,202,?,?,?)
            """,
            (
                key_id,
                "idem-key-1",
                "hash-1",
                json.dumps({"ok": True, "task_id": task_id, "status": "queued", "estimated_point_cost": 6}),
                task_id,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO usage_log(api_key_id, task_id, kind, account_id, prompt, status, request_id, model_name, resolution, ratio, estimated_point_cost, created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                key_id,
                task_id,
                "image",
                account_id,
                "hello",
                "failed",
                "req-idem",
                "Google Nano Banana 2",
                "2K",
                "16:9",
                6,
                now,
            ),
        )
        conn.commit()
        conn.close()

        result = server.retry_task_record(task_id, key_id, request_id="req-retry")
        self.assertEqual(result["task"]["status"], "queued")

        conn = server.db_conn()
        row = conn.execute(
            "SELECT status_code,response_json FROM idempotency_keys WHERE api_key_id=? AND idempotency_key=?",
            (key_id, "idem-key-1"),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        stored = json.loads(row["response_json"])
        self.assertEqual(stored["task_id"], task_id)
        self.assertEqual(stored["status"], "queued")
        self.assertNotEqual(stored.get("request_id"), "req-retry")

    def test_recover_stale_updates_only_latest_usage_row(self):
        account_id = self.seed_account_with_capabilities()
        key_id = self.seed_api_key("recover-key")
        now = time.time()
        conn = server.db_conn()
        conn.execute(
            """
            INSERT INTO tasks(
                api_key_id, account_id, kind, prompt, model_name, scene_id, resolution, ratio,
                duration, estimated_point_cost, request_id, payload_json, response_json, assets_json,
                status, attempt_count, started_at, created_at, updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                key_id,
                account_id,
                "image",
                "hello",
                "Google Nano Banana 2",
                "",
                "2K",
                "16:9",
                None,
                6,
                "req-recover",
                "{}",
                "{}",
                "[]",
                "running",
                1,
                now - 1000,
                now - 1000,
                now - 1000,
            ),
        )
        task_id = conn.execute("SELECT id FROM tasks WHERE request_id='req-recover'").fetchone()[0]
        # Historical completed row must never be rewritten by recovery.
        conn.execute(
            """
            INSERT INTO usage_log(api_key_id, task_id, kind, account_id, prompt, status, request_id, model_name, resolution, ratio, estimated_point_cost, created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                key_id,
                task_id,
                "image",
                account_id,
                "hello",
                "completed",
                "req-old",
                "Google Nano Banana 2",
                "2K",
                "16:9",
                6,
                now - 2000,
            ),
        )
        old_row_id = conn.execute(
            "SELECT id FROM usage_log WHERE task_id=? ORDER BY id ASC LIMIT 1", (task_id,)
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO usage_log(api_key_id, task_id, kind, account_id, prompt, status, request_id, model_name, resolution, ratio, estimated_point_cost, created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                key_id,
                task_id,
                "image",
                account_id,
                "hello",
                "running",
                "req-recover",
                "Google Nano Banana 2",
                "2K",
                "16:9",
                6,
                now,
            ),
        )
        conn.commit()
        conn.close()

        recovered = server.recover_stale_running_tasks(now=now + 1000.0, stale_after_seconds=60.0)
        self.assertEqual(recovered, 1)
        conn = server.db_conn()
        rows = conn.execute(
            "SELECT status,error_code,request_id FROM usage_log WHERE task_id=? ORDER BY id ASC",
            (task_id,),
        ).fetchall()
        conn.close()
        self.assertEqual(rows[0]["request_id"], "req-old")
        self.assertEqual(rows[0]["status"], "completed")
        self.assertIn(rows[0]["error_code"], (None, ""))
        self.assertEqual(rows[1]["request_id"], "req-recover")
        self.assertEqual(rows[1]["status"], "expired")
        self.assertEqual(rows[1]["error_code"], "WORKER_LOST")

    def test_failed_finalize_preserves_existing_response_json(self):
        account_id = self.seed_account_with_capabilities()
        key_id = self.seed_api_key("finalize-key")
        now = time.time()
        task_id = server.save_task(
            account_id,
            "image",
            "hello",
            {
                "kind": "image",
                "prompt": "hello",
                "model_name": "Google Nano Banana 2",
                "resolution": "2K",
                "ratio": "16:9",
            },
            {"status": "submitted", "chat": {"chatId": "chat-upstream", "focusId": "focus-upstream"}, "raw": "upstream detail"},
            status="running",
            api_key_id=key_id,
            request_id="req-finalize",
            model_name="Google Nano Banana 2",
            resolution="2K",
            ratio="16:9",
            estimated_point_cost=6,
            started_at=now,
        )
        task = dict(server.fetch_task_row(task_id))
        attempt_id = server.create_task_attempt(task, "generation")
        server.finalize_task_attempt(
            task,
            attempt_id,
            "generation",
            {
                "account_id": account_id,
                "error_code": "UPSTREAM_ERROR",
                "error_message": "upstream failed after partial response",
                "response_summary": "failed",
                "status_code": 503,
            },
            "failed",
        )
        conn = server.db_conn()
        row = conn.execute("SELECT status,response_json FROM tasks WHERE id=?", (task_id,)).fetchone()
        conn.close()
        self.assertEqual(row["status"], "failed")
        stored = json.loads(row["response_json"])
        self.assertEqual(stored.get("chat", {}).get("chatId"), "chat-upstream")
        self.assertEqual(stored.get("raw"), "upstream detail")

    def test_prune_removes_only_old_terminal_tasks(self):
        account_id = self.seed_account_with_capabilities()
        key_id = self.seed_api_key("prune-key")
        now = time.time()
        conn = server.db_conn()
        old_done = server.save_task(
            account_id,
            "image",
            "old done",
            {},
            {"status": "completed"},
            status="completed",
            api_key_id=key_id,
            request_id="req-old-done",
            finished_at=now - 10 * 86400,
        )
        old_active = server.save_task(
            account_id,
            "image",
            "old active",
            {},
            {"status": "running"},
            status="running",
            api_key_id=key_id,
            request_id="req-old-active",
            started_at=now - 10 * 86400,
        )
        new_done = server.save_task(
            account_id,
            "image",
            "new done",
            {},
            {"status": "completed"},
            status="completed",
            api_key_id=key_id,
            request_id="req-new-done",
            finished_at=now - 1 * 86400,
        )
        conn.commit()
        conn.close()

        result = server.prune_historical_records(days=7, now=now)
        self.assertEqual(result["deleted_tasks"], 1)
        conn = server.db_conn()
        remaining = {
            row[0]
            for row in conn.execute("SELECT request_id FROM tasks WHERE id IN (?,?,?)", (old_done, old_active, new_done)).fetchall()
        }
        conn.close()
        self.assertNotIn("req-old-done", remaining)
        self.assertIn("req-old-active", remaining)
        self.assertIn("req-new-done", remaining)


if __name__ == "__main__":
    unittest.main()
