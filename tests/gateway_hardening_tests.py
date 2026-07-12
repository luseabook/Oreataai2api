import json
import shutil
import struct
import subprocess
import threading
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import server

TEST_ENCRYPTION_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


class GatewayHardeningTests(unittest.TestCase):
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

    def sample_mp4_bytes(self, duration_sec=3, width=320, height=240):
        def box(name, payload):
            return struct.pack(">I4s", len(payload) + 8, name.encode("ascii")) + payload

        mvhd_payload = (
            b"\x00\x00\x00\x00"
            + b"\x00" * 8
            + struct.pack(">II", 1000, int(duration_sec * 1000))
            + b"\x00" * 80
        )
        tkhd_payload = (
            b"\x00" * 76
            + struct.pack(">II", int(width * 65536), int(height * 65536))
        )
        return box("ftyp", b"isom" + b"\x00" * 12) + box("moov", box("mvhd", mvhd_payload) + box("trak", box("tkhd", tkhd_payload)))

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
                            "pointCostMotion": [{"duration": 5, "point": 30, "aiType": 15123}],
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
                        },
                        {
                            "sceneId": "frame_based",
                            "sceneName": {"zh": "首尾帧"},
                            "description": {"zh": "上传首帧和尾帧"},
                            "sceneIcon": "frame.svg",
                        },
                        {
                            "sceneId": "reference",
                            "sceneName": {"zh": "参考素材"},
                            "description": {"zh": "上传参考图片或视频"},
                            "sceneIcon": "reference.svg",
                        },
                        {
                            "sceneId": "motion",
                            "sceneName": {"zh": "动作模仿"},
                            "description": {"zh": "上传角色图和动作视频"},
                            "sceneIcon": "motion.svg",
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
                server.encrypt_secret_value("plain-password"),
                "verified",
                "manual",
                server.encrypt_secret_value("ouid-secret"),
                server.encrypt_secret_value("ouss-secret"),
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

    def valid_image_request(self, sync_wait_seconds=None):
        request = {
            "kind": "image",
            "prompt": "hello",
            "model_name": "Google Nano Banana 2",
            "resolution": "4K",
            "ratio": "16:9",
        }
        if sync_wait_seconds is not None:
            request["sync_wait_seconds"] = sync_wait_seconds
        return request

    def valid_video_request(self, scene_id="text_or_image", sync_wait_seconds=None):
        request = {
            "kind": "video",
            "prompt": "hello",
            "model_name": "Seedance 2.0 Mini",
            "resolution": "480",
            "ratio": "16:9",
            "duration": 5,
            "scene_id": scene_id,
        }
        if sync_wait_seconds is not None:
            request["sync_wait_seconds"] = sync_wait_seconds
        return request

    def uploaded_image(self, name="first.png", object_path="uploads/first.png"):
        return {
            "fileName": name.rsplit(".", 1)[0],
            "fileExt": name.rsplit(".", 1)[1],
            "originSize": 1234,
            "object": object_path,
            "status": "completed",
        }

    def uploaded_video(self, name="motion.mp4", object_path="uploads/motion.mp4", duration=8):
        return {
            "fileName": name.rsplit(".", 1)[0],
            "fileExt": name.rsplit(".", 1)[1],
            "originSize": 9876,
            "object": object_path,
            "status": "completed",
            "videoDurationSec": duration,
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

    def process_task_queue(self, limit=1):
        return server.process_task_queue(limit=limit)

    def post_generation_requests_concurrently(self, api_key, requests):
        result_lock = threading.Lock()
        request_start = threading.Barrier(len(requests))
        responses = []
        errors = []

        def post_generation(request):
            client = TestClient(server.app)
            try:
                request_start.wait(timeout=5)
                response = client.post(
                    "/v1/generate",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=request,
                )
                with result_lock:
                    responses.append(response)
            except Exception as exc:
                with result_lock:
                    errors.append(exc)
            finally:
                client.close()

        threads = [threading.Thread(target=post_generation, args=(request,)) for request in requests]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        return responses

    def post_upload_requests_concurrently(self, api_key, files):
        result_lock = threading.Lock()
        request_start = threading.Barrier(len(files))
        responses = []
        errors = []

        def post_upload(file_spec):
            client = TestClient(server.app)
            try:
                request_start.wait(timeout=5)
                response = client.post(
                    "/v1/uploads",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": file_spec},
                )
                with result_lock:
                    responses.append(response)
            except Exception as exc:
                with result_lock:
                    errors.append(exc)
            finally:
                client.close()

        threads = [threading.Thread(target=post_upload, args=(file_spec,)) for file_spec in files]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        return responses

    def seed_task(
        self,
        *,
        status="queued",
        kind="video",
        response=None,
        chat_id="chat-seeded",
        cancel_requested_at=None,
        started_at=None,
        finished_at=None,
    ):
        account_id = self.seed_account_with_capabilities(email=f"seeded-{time.time_ns()}@example.com")
        body = self.valid_video_request() if kind == "video" else self.valid_image_request()
        task_id = server.save_task(
            account_id,
            kind,
            body["prompt"],
            body,
            response or {"status": status, "chat": {"chatId": chat_id, "focusId": f"{chat_id}-focus"}},
            status=status,
            model_name=body.get("model_name") or "",
            scene_id=body.get("scene_id") or "",
            resolution=body.get("resolution") or "",
            ratio=body.get("ratio") or "",
            duration=body.get("duration"),
            cancel_requested_at=cancel_requested_at,
            started_at=started_at,
            finished_at=finished_at,
        )
        return task_id

    def seed_running_task_after_claim_create_gap(self, api_key):
        self.seed_account_with_capabilities()
        api_key_id = self.seed_api_key(api_key)
        created = self.client.post(
            "/v1/generate",
            headers={"Authorization": f"Bearer {api_key}"},
            json=self.valid_image_request(),
        )
        self.assertEqual(created.status_code, 202)
        task_id = created.json()["task_id"]

        abandoned_claim = server.claim_next_task()
        self.assertEqual(abandoned_claim["id"], task_id)
        server.update_task_record(task_id, updated_at=900.0)
        self.assertEqual(
            server.recover_stale_running_tasks(now=1000.0, stale_after_seconds=60.0),
            1,
        )
        retried = self.client.post(
            f"/v1/tasks/{task_id}/retry",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        self.assertEqual(retried.status_code, 200)

        current_task = server.claim_next_task()
        self.assertEqual(current_task["id"], task_id)
        attempt_id = server.create_task_attempt(current_task, "generation")
        conn = server.db_conn()
        task_attempt_count = conn.execute(
            "SELECT attempt_count FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()["attempt_count"]
        attempt_no = conn.execute(
            "SELECT attempt_no FROM task_attempts WHERE id=?",
            (attempt_id,),
        ).fetchone()["attempt_no"]
        conn.close()
        self.assertEqual(task_attempt_count, 2)
        self.assertEqual(attempt_no, 1)
        return api_key_id, task_id, current_task, attempt_id

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
        task_cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        attempt_cols = {r["name"] for r in conn.execute("PRAGMA table_info(task_attempts)").fetchall()}
        usage_cols = {r["name"] for r in conn.execute("PRAGMA table_info(usage_log)").fetchall()}
        idem = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='idempotency_keys'").fetchone()
        conn.close()

        self.assertIn("rate_limit_per_minute", api_key_cols)
        self.assertIn("daily_request_limit", api_key_cols)
        self.assertIn("daily_point_limit", api_key_cols)
        self.assertIn("deleted_at", api_key_cols)
        self.assertIn("last_used_at", account_cols)
        self.assertIn("failure_count", account_cols)
        self.assertIn("cooldown_until", account_cols)
        self.assertIn("status", task_cols)
        self.assertIn("request_id", task_cols)
        self.assertIn("api_key_id", task_cols)
        self.assertIn("attempt_count", task_cols)
        self.assertIn("cancel_requested_at", task_cols)
        self.assertIn("phase", attempt_cols)
        self.assertIn("attempt_no", attempt_cols)
        self.assertIn("request_payload_json", attempt_cols)
        self.assertIn("stream_summary_json", attempt_cols)
        self.assertIn("hydration_summary_json", attempt_cols)
        self.assertIn("request_id", usage_cols)
        self.assertIn("idempotency_key", usage_cols)
        self.assertIn("model_name", usage_cols)
        self.assertIn("estimated_point_cost", usage_cols)
        self.assertIn("error_code", usage_cols)
        self.assertIn("status_code", usage_cols)
        self.assertIsNotNone(idem)

    def test_gateway_hardening_schema_has_balance_snapshot_columns(self):
        conn = server.db_conn()
        account_cols = {r["name"] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()}
        task_cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        usage_cols = {r["name"] for r in conn.execute("PRAGMA table_info(usage_log)").fetchall()}
        api_key_cols = {r["name"] for r in conn.execute("PRAGMA table_info(api_keys)").fetchall()}
        client_table = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='clients'").fetchone()
        conn.close()

        self.assertIn("point_balance_json", account_cols)
        self.assertIn("rest_point", account_cols)
        self.assertIn("daily_point", account_cols)
        self.assertIn("bonus_point", account_cols)
        self.assertIn("balance_updated_at", account_cols)
        self.assertIn("balance_before_json", task_cols)
        self.assertIn("balance_after_json", task_cols)
        self.assertIn("balance_before_rest_point", task_cols)
        self.assertIn("balance_after_rest_point", task_cols)
        self.assertIn("balance_before_daily_point", task_cols)
        self.assertIn("balance_after_daily_point", task_cols)
        self.assertIn("balance_before_bonus_point", task_cols)
        self.assertIn("balance_after_bonus_point", task_cols)
        self.assertIn("actual_point_cost", task_cols)
        self.assertIn("actual_point_cost", usage_cols)
        self.assertIn("client_id", api_key_cols)
        self.assertIsNotNone(client_table)

    def test_database_connections_enable_production_pragmas_and_indexes(self):
        server.init_db()
        conn = server.db_conn()
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        index_names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
            ).fetchall()
        }
        migration = conn.execute(
            "SELECT version,name FROM schema_migrations WHERE version=1"
        ).fetchone()
        conn.close()

        self.assertEqual(foreign_keys, 1)
        self.assertGreaterEqual(busy_timeout, 5000)
        self.assertEqual(str(journal_mode).lower(), "wal")
        self.assertTrue(
            {
                "idx_tasks_claim",
                "idx_tasks_tenant_lookup",
                "idx_usage_quota_window",
                "idx_usage_task_lookup",
                "idx_idempotency_task_lookup",
                "idx_accounts_scheduler",
                "idx_task_attempts_task",
            }.issubset(index_names)
        )
        self.assertIsNotNone(migration)
        self.assertEqual(migration["name"], "operational_indexes")

    def test_capabilities_expose_scene_policy_metadata(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("caps-key")

        response = self.client.get("/v1/capabilities", headers={"Authorization": "Bearer caps-key"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        scenes = {scene["scene_id"]: scene for scene in payload["video"]["scenes"]}
        self.assertTrue(scenes["text_or_image"]["enabled"])
        self.assertEqual(scenes["text_or_image"]["verification_status"], "live_verified")
        self.assertFalse(scenes["reference"]["enabled"])
        self.assertTrue(scenes["reference"]["experimental"])
        self.assertEqual(scenes["reference"]["verification_status"], "unverified")
        self.assertFalse(scenes["frame_based"]["enabled"])
        self.assertTrue(scenes["motion"]["experimental"])

    def test_generate_rejects_experimental_scene_before_upstream_call(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("scene-key")

        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "create_chat_session", return_value={"chatId": "chat-exp"}) as create_chat,
        ):
            response = self.client.post(
                "/v1/generate",
                headers={"Authorization": "Bearer scene-key"},
                json={
                    "kind": "video",
                    "prompt": "hello",
                    "model_name": "Seedance 2.0 Mini",
                    "resolution": "480",
                    "ratio": "16:9",
                    "duration": 5,
                    "scene_id": "reference",
                },
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "EXPERIMENTAL_SCENE_DISABLED")
        create_chat.assert_not_called()

    def test_tls_verify_setting_applies_to_new_sessions(self):
        original_cfg = server.CFG
        server.CFG = server.deep_merge(original_cfg, {"oreate": {"verify_tls": False}})
        try:
            class FakeSession:
                def __init__(self):
                    self.verify = True
                    self.cookies = {}
                    self.called = False

                def get(self, *args, **kwargs):
                    self.called = True
                    return type("Resp", (), {"raise_for_status": lambda self: None})()

            fake = FakeSession()
            with patch.object(server.requests, "Session", return_value=fake):
                client = server.OreateClient()
                session = client.new_session()
        finally:
            server.CFG = original_cfg

        self.assertIs(session, fake)
        self.assertTrue(fake.called)
        self.assertFalse(fake.verify)

    def test_auto_register_accounts_uses_configured_tls_verify_flag_for_confirmation_link(self):
        original_cfg = server.CFG
        server.CFG = server.deep_merge(original_cfg, {"oreate": {"verify_tls": True}})
        try:
            with (
                patch.object(server.MAIL, "create_mailbox", return_value={"address": "user@example.com", "token": "mail-token", "domain": "example.com", "mailbox_id": "mb-1"}),
                patch.object(
                    server.CLIENT,
                    "signup_attempt",
                    return_value={
                        "status_code": 200,
                        "response": {"status": {"code": 0}, "data": {"sendEmailCount": 1, "confirmEmailStatus": 1, "registerStatus": 1}},
                        "ticket": {"ticketID": "ticket-1"},
                        "cookies": {},
                    },
                ),
                patch.object(server.MAIL, "wait_verification_artifact", return_value={"link": "https://www.oreateai.com/passport/confirm?tokenID=abc123", "code": ""}),
                patch.object(server.requests, "get") as visit_link,
                patch.object(server.CLIENT, "confirm_email_register", return_value={"status_code": 200, "response": {"status": {"code": 0}}}),
                patch.object(server.CLIENT, "login", return_value=server.OreateSession(email="user@example.com", password="pass", cookies={"OUID": "ouid", "ouss": "ouss"})),
                patch.object(server.CLIENT, "session_from_cookie_dict", return_value=object()),
                patch.object(server.CLIENT, "fetch_image_models", return_value={}),
                patch.object(server.CLIENT, "fetch_video_models", return_value=[]),
                patch.object(server.CLIENT, "fetch_video_scenes", return_value=[]),
                patch.object(server, "save_account", return_value=1) as save_account,
                patch.object(
                    server,
                    "validate_registered_account",
                    return_value={"ok": True, "asset_count": 1},
                ),
            ):
                result = server.auto_register_accounts(1)
        finally:
            server.CFG = original_cfg

        self.assertEqual(result[0]["status"], "verified")
        self.assertEqual(save_account.call_args.kwargs["status"], "pending_validation")
        self.assertTrue(visit_link.called)
        self.assertTrue(visit_link.call_args.kwargs["verify"])

    def test_registered_account_validation_is_deferred_when_gateway_environment_is_risk_controlled(self):
        session = server.OreateSession(
            email="risk@example.com",
            password="password",
            cookies={"OUID": "ouid", "ouss": "ouss"},
        )
        trace = []
        risk_error = server.UpstreamGenerationError(
            {"code": "212361", "message": "spam user"}
        )

        with (
            patch.object(server, "save_account", return_value=7) as save_account,
            patch.object(
                server,
                "validate_registered_account",
                side_effect=risk_error,
            ),
            patch.object(server, "mark_account_failure") as mark_failure,
            patch.object(server, "isolate_account_from_pool") as isolate,
        ):
            account_id, status = server.save_and_validate_registered_account(
                "risk@example.com",
                "password",
                session,
                self.sample_image_info(),
                self.sample_video_info(),
                trace,
            )

        self.assertEqual(account_id, 7)
        self.assertEqual(status, "validation_deferred")
        self.assertEqual(save_account.call_args.kwargs["status"], "pending_validation")
        mark_failure.assert_called_once_with(7, risk_error)
        isolate.assert_not_called()
        self.assertEqual(trace[-1]["step"], "generation_validation")
        self.assertEqual(trace[-1]["status"], "validation_deferred")

    def test_upstream_error_code_ignores_numeric_email_domain_and_reads_status_code(self):
        error = RuntimeError(
            "getpointdetail failed for user@100811.xyz: "
            "getpointdetail failed: {'status': {'code': 200001, "
            "'msg': 'user not login'}, 'sLogid': '3232201747'}"
        )

        self.assertEqual(server.upstream_error_code(error), "200001")

    def test_generate_enqueues_task_and_worker_completes_it(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("async-key")
        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "create_chat_session", return_value={"chatId": "chat-async", "focusId": "focus-async"}),
            patch.object(server.CLIENT, "stream_generation", return_value={"events": [{"event": "end"}], "error": None, "status": "streamed"}),
            patch.object(server.CLIENT, "hydrate_generation_result", return_value={"raw": {}, "assets": ["https://cdn.oreateai.com/static/result/x.jpg"], "status": "completed"}),
        ):
            response = self.client.post("/v1/generate", headers={"Authorization": "Bearer async-key"}, json=self.valid_image_request())

            self.assertEqual(response.status_code, 202)
            payload = response.json()
            self.assertEqual(payload["status"], "queued")
            self.assertIn("task_id", payload)

            processed = self.process_task_queue(limit=1)
            self.assertEqual(processed, 1)

            detail = self.client.get(f"/v1/tasks/{payload['task_id']}", headers={"Authorization": "Bearer async-key"})
            self.assertEqual(detail.status_code, 200)
            task = detail.json()["task"]
            self.assertEqual(task["status"], "completed")
            self.assertEqual(task["assets"], ["https://cdn.oreateai.com/static/result/x.jpg"])
            self.assertEqual(task["attempt_count"], 1)

    def test_task_retry_cancel_and_hydrate_actions_work(self):
        self.seed_account_with_capabilities()
        self.seed_account_with_capabilities("task-action-failover@example.com")
        self.seed_api_key("task-action-key")
        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "create_chat_session", side_effect=RuntimeError("upstream down")),
        ):
            created = self.client.post("/v1/generate", headers={"Authorization": "Bearer task-action-key"}, json=self.valid_image_request())

            self.assertEqual(created.status_code, 202)
            task_id = created.json()["task_id"]
            self.process_task_queue(limit=1)

            detail = self.client.get(f"/v1/tasks/{task_id}", headers={"Authorization": "Bearer task-action-key"})
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.json()["task"]["status"], "failed")

            retry = self.client.post(f"/v1/tasks/{task_id}/retry", headers={"Authorization": "Bearer task-action-key"})
            self.assertEqual(retry.status_code, 200)
            self.assertEqual(retry.json()["task"]["status"], "queued")

            cancel = self.client.post(f"/v1/tasks/{task_id}/cancel", headers={"Authorization": "Bearer task-action-key"})
            self.assertEqual(cancel.status_code, 200)
            self.assertEqual(cancel.json()["task"]["status"], "cancelled")

    def test_terminal_tasks_cannot_be_cancelled(self):
        headers = self.admin_headers()
        for status in ("completed", "failed", "expired"):
            with self.subTest(status=status):
                task_id = self.seed_task(
                    status=status,
                    response={
                        "status": status,
                        "chat": {"chatId": f"chat-{status}", "focusId": f"focus-{status}"},
                        "assets": [f"https://cdn.oreateai.com/{status}.mp4"],
                    },
                    finished_at=time.time(),
                )
                conn = server.db_conn()
                before = dict(
                    conn.execute(
                        """
                        SELECT status,response_json,assets_json,chat_id,focus_id,
                               error_code,error_message,cancel_requested_at,finished_at
                        FROM tasks WHERE id=?
                        """,
                        (task_id,),
                    ).fetchone()
                )
                conn.close()

                with (
                    patch.object(server, "update_task_record") as update_task,
                    patch.object(server, "update_usage_log_for_task") as update_usage,
                ):
                    response = self.client.post(
                        f"/api/tasks/{task_id}/cancel",
                        headers=headers,
                    )

                self.assertEqual(response.status_code, 409)
                payload = response.json()
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["error"]["code"], "TASK_NOT_CANCELLABLE")
                self.assertIn(status, payload["error"]["details"]["status"])
                update_task.assert_not_called()
                update_usage.assert_not_called()

                conn = server.db_conn()
                after = dict(
                    conn.execute(
                        """
                        SELECT status,response_json,assets_json,chat_id,focus_id,
                               error_code,error_message,cancel_requested_at,finished_at
                        FROM tasks WHERE id=?
                        """,
                        (task_id,),
                    ).fetchone()
                )
                conn.close()
                self.assertEqual(after, before)

    def test_task_cancel_is_allowed_for_active_states_and_idempotent_after_cancel(self):
        headers = self.admin_headers()
        for status in ("queued", "running", "submitted", "hydrating"):
            with self.subTest(status=status):
                task_id = self.seed_task(
                    status=status,
                    started_at=time.time() if status == "running" else None,
                )

                first = self.client.post(f"/api/tasks/{task_id}/cancel", headers=headers)

                self.assertEqual(first.status_code, 200)
                first_task = first.json()["task"]
                self.assertEqual(first_task["status"], "cancelled")
                self.assertEqual(first_task["error_code"], "TASK_CANCELLED")
                first_cancel_requested_at = first_task["cancel_requested_at"]
                first_finished_at = first_task["finished_at"]

                with (
                    patch.object(server, "update_task_record") as update_task,
                    patch.object(server, "update_usage_log_for_task") as update_usage,
                ):
                    second = self.client.post(f"/api/tasks/{task_id}/cancel", headers=headers)

                self.assertEqual(second.status_code, 200)
                second_task = second.json()["task"]
                self.assertEqual(second_task["status"], "cancelled")
                self.assertEqual(second_task["cancel_requested_at"], first_cancel_requested_at)
                self.assertEqual(second_task["finished_at"], first_finished_at)
                update_task.assert_not_called()
                update_usage.assert_not_called()

    def test_scoped_cancel_supports_legacy_usage_ownership_without_cross_tenant_access(self):
        owner_key_id = self.seed_api_key("legacy-task-owner")
        self.seed_api_key("legacy-task-foreign")
        task_id = self.seed_task(status="queued", kind="image")
        task = server.fetch_task_row(task_id)
        self.assertIsNotNone(task)
        server.log_usage(
            owner_key_id,
            task["kind"],
            task["account_id"],
            task["prompt"],
            "queued",
            task_id=task_id,
            status_code=202,
        )

        foreign = self.client.post(
            f"/v1/tasks/{task_id}/cancel",
            headers={"Authorization": "Bearer legacy-task-foreign"},
        )

        self.assertEqual(foreign.status_code, 404)
        self.assertEqual(foreign.json()["error"]["code"], "TASK_NOT_FOUND")

        cancelled = self.client.post(
            f"/v1/tasks/{task_id}/cancel",
            headers={"Authorization": "Bearer legacy-task-owner"},
        )

        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["task"]["status"], "cancelled")
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

    def test_task_retry_rechecks_api_key_model_scope(self):
        self.seed_account_with_capabilities()
        self.seed_account_with_capabilities("retry-scope-failover@example.com")
        key_id = self.seed_api_key("retry-scope-key")
        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "create_chat_session", side_effect=RuntimeError("upstream down")),
        ):
            created = self.client.post(
                "/v1/generate",
                headers={"Authorization": "Bearer retry-scope-key"},
                json=self.valid_image_request(),
            )
            task_id = created.json()["task_id"]
            self.process_task_queue(limit=1)

        conn = server.db_conn()
        conn.execute(
            "UPDATE api_keys SET allowed_models=? WHERE id=?",
            (json.dumps(["another-model"]), key_id),
        )
        conn.commit()
        conn.close()

        retry = self.client.post(
            f"/v1/tasks/{task_id}/retry",
            headers={"Authorization": "Bearer retry-scope-key"},
        )

        self.assertEqual(retry.status_code, 403)
        self.assertEqual(retry.json()["error"]["code"], "API_KEY_MODEL_FORBIDDEN")
        conn = server.db_conn()
        task = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        usage_count = conn.execute("SELECT COUNT(*) FROM usage_log WHERE task_id=?", (task_id,)).fetchone()[0]
        conn.close()
        self.assertEqual(task["status"], "failed")
        self.assertEqual(usage_count, 1)

    def test_task_retry_rechecks_daily_point_quota(self):
        self.seed_account_with_capabilities()
        self.seed_account_with_capabilities("retry-quota-failover@example.com")
        self.seed_api_key("retry-quota-key", daily_point_limit=12)
        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "create_chat_session", side_effect=RuntimeError("upstream down")),
        ):
            created = self.client.post(
                "/v1/generate",
                headers={"Authorization": "Bearer retry-quota-key"},
                json=self.valid_image_request(),
            )
            task_id = created.json()["task_id"]
            self.process_task_queue(limit=1)

        retry = self.client.post(
            f"/v1/tasks/{task_id}/retry",
            headers={"Authorization": "Bearer retry-quota-key"},
        )

        self.assertEqual(retry.status_code, 429)
        self.assertEqual(retry.json()["error"]["code"], "DAILY_POINT_LIMIT_EXCEEDED")

    def test_concurrent_task_retries_share_daily_request_admission(self):
        api_key_id = self.seed_api_key("concurrent-retry-key", daily_request_limit=1)
        task_ids = [self.seed_task(status="failed", kind="image") for _ in range(2)]
        conn = server.db_conn()
        conn.executemany(
            "UPDATE tasks SET api_key_id=? WHERE id=?",
            [(api_key_id, task_id) for task_id in task_ids],
        )
        conn.commit()
        conn.close()

        original_check_daily_quota = server.check_daily_quota
        quota_checked = threading.Barrier(2)

        def synchronized_check_daily_quota(*args, **kwargs):
            original_check_daily_quota(*args, **kwargs)
            try:
                quota_checked.wait(timeout=0.5)
            except threading.BrokenBarrierError:
                pass

        result_lock = threading.Lock()
        request_start = threading.Barrier(2)
        responses = []
        errors = []

        def retry_task(task_id):
            client = TestClient(server.app)
            try:
                request_start.wait(timeout=5)
                response = client.post(
                    f"/v1/tasks/{task_id}/retry",
                    headers={"Authorization": "Bearer concurrent-retry-key"},
                )
                with result_lock:
                    responses.append(response)
            except Exception as exc:
                with result_lock:
                    errors.append(exc)
            finally:
                client.close()

        threads = [threading.Thread(target=retry_task, args=(task_id,)) for task_id in task_ids]
        with patch.object(server, "check_daily_quota", side_effect=synchronized_check_daily_quota):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(sorted(response.status_code for response in responses), [200, 429])
        rejected = next(response for response in responses if response.status_code == 429)
        self.assertEqual(rejected.json()["error"]["code"], "DAILY_REQUEST_LIMIT_EXCEEDED")

        conn = server.db_conn()
        statuses = [
            row["status"]
            for row in conn.execute(
                "SELECT status FROM tasks WHERE id IN (?,?) ORDER BY id",
                task_ids,
            ).fetchall()
        ]
        usage_count = conn.execute(
            "SELECT COUNT(*) FROM usage_log WHERE api_key_id=?",
            (api_key_id,),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(sorted(statuses), ["failed", "queued"])
        self.assertEqual(usage_count, 1)

    def test_task_retry_selects_healthy_account_and_records_new_billable_attempt(self):
        self.seed_account_with_capabilities("retry-first@example.com")
        self.seed_account_with_capabilities("retry-second@example.com")
        self.seed_api_key("retry-failover-key")
        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "create_chat_session", side_effect=RuntimeError("upstream down")),
        ):
            created = self.client.post(
                "/v1/generate",
                headers={"Authorization": "Bearer retry-failover-key"},
                json=self.valid_image_request(),
            )
            task_id = created.json()["task_id"]
            original_account_id = created.json()["account_id"]
            self.process_task_queue(limit=1)

        retry = self.client.post(
            f"/v1/tasks/{task_id}/retry",
            headers={"Authorization": "Bearer retry-failover-key"},
        )

        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json()["task"]["status"], "queued")
        self.assertNotEqual(retry.json()["task"]["account_id"], original_account_id)
        conn = server.db_conn()
        usage_rows = conn.execute(
            "SELECT estimated_point_cost,status FROM usage_log WHERE task_id=? ORDER BY id",
            (task_id,),
        ).fetchall()
        conn.close()
        self.assertEqual(len(usage_rows), 2)
        self.assertEqual([row["estimated_point_cost"] for row in usage_rows], [12, 12])
        self.assertEqual(usage_rows[-1]["status"], "queued")

    def test_task_hydrate_reprocesses_submitted_task(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("hydrate-key")
        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "create_chat_session", return_value={"chatId": "chat-hydrate", "focusId": "focus-hydrate"}),
            patch.object(server.CLIENT, "stream_generation", return_value={"events": [{"event": "ping"}], "error": None, "status": "submitted"}),
            patch.object(server.CLIENT, "hydrate_generation_result_until_assets", side_effect=[
                {"raw": {}, "assets": [], "status": "submitted", "attempts": 1},
                {"raw": {"status": {"code": 0}, "data": {"messageList": [{"content": '<video src="https://cdn.oreateai.com/aivideo/videodownload/1899992928.mp4">'}]}}, "assets": ["https://cdn.oreateai.com/aivideo/videodownload/1899992928.mp4"], "status": "completed", "attempts": 2},
            ]),
        ):
            created = self.client.post(
                "/v1/generate",
                headers={"Authorization": "Bearer hydrate-key"},
                json={
                    "kind": "video",
                    "prompt": "hello",
                    "model_name": "Seedance 2.0 Mini",
                    "resolution": "480",
                    "ratio": "16:9",
                    "duration": 5,
                    "scene_id": "text_or_image",
                },
            )

            self.assertEqual(created.status_code, 202)
            task_id = created.json()["task_id"]
            self.process_task_queue(limit=1)

            detail = self.client.get(f"/v1/tasks/{task_id}", headers={"Authorization": "Bearer hydrate-key"})
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.json()["task"]["status"], "submitted")

            hydrate = self.client.post(f"/v1/tasks/{task_id}/hydrate", headers={"Authorization": "Bearer hydrate-key"})
            self.assertEqual(hydrate.status_code, 200)
            self.assertEqual(hydrate.json()["task"]["status"], "hydrating")

            self.process_task_queue(limit=1)

            refreshed = self.client.get(f"/v1/tasks/{task_id}", headers={"Authorization": "Bearer hydrate-key"})
            self.assertEqual(refreshed.status_code, 200)
            task = refreshed.json()["task"]
            self.assertEqual(task["status"], "completed")
            self.assertEqual(task["assets"], ["https://cdn.oreateai.com/aivideo/videodownload/1899992928.mp4"])

    def test_cancelled_running_task_does_not_write_completed_result_after_worker_returns(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("cancel-midflight-key")
        proceed = threading.Event()
        release = threading.Event()

        def slow_generation(task, attempt_id):
            proceed.set()
            self.assertTrue(release.wait(timeout=2), "test worker was not released")
            return {
                "account_id": task["account_id"],
                "body": server.GatewayGenerateIn(**self.valid_image_request()),
                "options": {
                    "model_name": "Google Nano Banana 2",
                    "resolution": "4K",
                    "ratio": "16:9",
                },
                "generation": {
                    "response": {"chat": {"chatId": "chat-cancelled", "focusId": "focus-cancelled"}},
                    "assets": ["https://cdn.oreateai.com/static/result/should-not-stick.jpg"],
                    "stream": {"events": [{"event": "end"}]},
                    "hydration": {"assets": ["https://cdn.oreateai.com/static/result/should-not-stick.jpg"]},
                    "status": "completed",
                },
            }

        with patch.object(server, "run_generation_attempt", side_effect=slow_generation):
            created = self.client.post(
                "/v1/generate",
                headers={"Authorization": "Bearer cancel-midflight-key"},
                json=self.valid_image_request(),
            )
            self.assertEqual(created.status_code, 202)
            task_id = created.json()["task_id"]

            worker = threading.Thread(target=self.process_task_queue, kwargs={"limit": 1})
            worker.start()
            self.assertTrue(proceed.wait(timeout=2), "worker did not enter generation attempt")

            cancelled = self.client.post(f"/v1/tasks/{task_id}/cancel", headers={"Authorization": "Bearer cancel-midflight-key"})
            self.assertEqual(cancelled.status_code, 200)
            self.assertEqual(cancelled.json()["task"]["status"], "cancelled")

            release.set()
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive(), "worker thread did not finish")

            detail = self.client.get(f"/v1/tasks/{task_id}", headers={"Authorization": "Bearer cancel-midflight-key"})
            self.assertEqual(detail.status_code, 200)
            task = detail.json()["task"]
            self.assertEqual(task["status"], "cancelled")
            self.assertEqual(task["assets"], [])

    def test_cancellation_wins_race_after_worker_check_before_final_write(self):
        self.seed_account_with_capabilities()
        api_key_id = self.seed_api_key("cancel-finalize-race-key")
        created = self.client.post(
            "/v1/generate",
            headers={"Authorization": "Bearer cancel-finalize-race-key"},
            json=self.valid_image_request(),
        )
        self.assertEqual(created.status_code, 202)
        task_id = created.json()["task_id"]
        task = server.claim_next_task()
        self.assertIsNotNone(task)
        self.assertEqual(task["id"], task_id)
        self.assertEqual(task["status"], "running")
        attempt_id = server.create_task_attempt(task, "generation")
        checked = threading.Event()
        release = threading.Event()
        worker_errors = []

        def stale_cancel_check(_task_id):
            checked.set()
            self.assertTrue(release.wait(timeout=2), "finalizer was not released")
            return False

        result = {
            "account_id": task["account_id"],
            "chat_id": "chat-race-lost",
            "focus_id": "focus-race-lost",
            "response_json": {
                "status": "completed",
                "chat": {"chatId": "chat-race-lost", "focusId": "focus-race-lost"},
            },
            "assets": ["https://cdn.oreateai.com/static/result/race-lost.jpg"],
            "response_summary": "completed",
            "status_code": 200,
            "actual_point_cost": 12,
        }

        def finalize():
            try:
                server.finalize_task_attempt(task, attempt_id, "generation", result, "completed")
            except Exception as exc:
                worker_errors.append(exc)

        with patch.object(server, "task_cancel_requested", side_effect=stale_cancel_check):
            worker = threading.Thread(target=finalize)
            worker.start()
            self.assertTrue(checked.wait(timeout=2), "worker did not reach the pre-finalization cancellation check")

            cancelled = self.client.post(
                f"/v1/tasks/{task_id}/cancel",
                headers={"Authorization": "Bearer cancel-finalize-race-key"},
            )
            self.assertEqual(cancelled.status_code, 200)
            self.assertEqual(cancelled.json()["task"]["status"], "cancelled")

            release.set()
            worker.join(timeout=2)

        self.assertFalse(worker.is_alive(), "worker finalizer did not finish")
        self.assertEqual(worker_errors, [])
        conn = server.db_conn()
        task_row = conn.execute(
            """
            SELECT status,assets_json,response_json,chat_id,focus_id,actual_point_cost,error_code
            FROM tasks WHERE id=?
            """,
            (task_id,),
        ).fetchone()
        attempt_row = conn.execute(
            "SELECT status,assets_json,error_code FROM task_attempts WHERE id=?",
            (attempt_id,),
        ).fetchone()
        usage_row = conn.execute(
            """
            SELECT status,response_summary,error_code,actual_point_cost,status_code
            FROM usage_log WHERE task_id=? AND api_key_id=?
            ORDER BY id DESC LIMIT 1
            """,
            (task_id, api_key_id),
        ).fetchone()
        conn.close()
        self.assertEqual(task_row["status"], "cancelled")
        self.assertEqual(json.loads(task_row["assets_json"]), [])
        self.assertNotEqual(task_row["chat_id"], "chat-race-lost")
        self.assertNotEqual(task_row["focus_id"], "focus-race-lost")
        self.assertIsNone(task_row["actual_point_cost"])
        self.assertEqual(task_row["error_code"], "TASK_CANCELLED")
        self.assertEqual(attempt_row["status"], "cancelled")
        self.assertEqual(json.loads(attempt_row["assets_json"]), [])
        self.assertEqual(attempt_row["error_code"], "TASK_CANCELLED")
        self.assertEqual(usage_row["status"], "cancelled")
        self.assertEqual(usage_row["response_summary"], "cancelled")
        self.assertEqual(usage_row["error_code"], "TASK_CANCELLED")
        self.assertIsNone(usage_row["actual_point_cost"])
        self.assertEqual(usage_row["status_code"], 499)

    def test_late_cancel_handler_preserves_completed_attempt_terminal_fields(self):
        task_id = self.seed_task(status="running", kind="image", started_at=time.time())
        task = dict(server.fetch_task_row(task_id))
        attempt_id = server.create_task_attempt(task, "generation")
        completed_at = time.time() - 10
        assets = ["https://cdn.oreateai.com/static/result/already-completed.jpg"]
        server.update_task_record(
            task_id,
            status="completed",
            assets_json=assets,
            error_code="",
            error_message="",
            finished_at=completed_at,
        )
        server.update_task_attempt(
            attempt_id,
            status="completed",
            error_code="UPSTREAM_RESULT_ACCEPTED",
            error_message="completed before late cancellation handler",
            assets_json=assets,
            finished_at=completed_at,
        )
        conn = server.db_conn()
        before = dict(
            conn.execute(
                """
                SELECT status,error_code,error_message,assets_json,finished_at
                FROM task_attempts WHERE id=?
                """,
                (attempt_id,),
            ).fetchone()
        )
        conn.close()

        server.cancel_task_attempt(task, attempt_id, "late cancellation handler")

        conn = server.db_conn()
        after = dict(
            conn.execute(
                """
                SELECT status,error_code,error_message,assets_json,finished_at
                FROM task_attempts WHERE id=?
                """,
                (attempt_id,),
            ).fetchone()
        )
        task_row = conn.execute(
            "SELECT status,assets_json,error_code FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(after, before)
        self.assertEqual(task_row["status"], "completed")
        self.assertEqual(json.loads(task_row["assets_json"]), assets)
        self.assertEqual(task_row["error_code"], "")

    def test_stale_attempt_cancel_cannot_cancel_retried_current_attempt(self):
        self.seed_account_with_capabilities()
        api_key_id = self.seed_api_key("stale-attempt-cancel-key")
        created = self.client.post(
            "/v1/generate",
            headers={"Authorization": "Bearer stale-attempt-cancel-key"},
            json=self.valid_image_request(),
        )
        self.assertEqual(created.status_code, 202)
        task_id = created.json()["task_id"]
        attempt_a_task = server.claim_next_task()
        self.assertEqual(attempt_a_task["id"], task_id)
        attempt_a_id = server.create_task_attempt(attempt_a_task, "generation")
        server.update_task_record(task_id, updated_at=900.0)
        self.assertEqual(
            server.recover_stale_running_tasks(now=1000.0, stale_after_seconds=60.0),
            1,
        )

        retried = self.client.post(
            f"/v1/tasks/{task_id}/retry",
            headers={"Authorization": "Bearer stale-attempt-cancel-key"},
        )
        self.assertEqual(retried.status_code, 200)
        attempt_b_task = server.claim_next_task()
        self.assertEqual(attempt_b_task["id"], task_id)
        attempt_b_id = server.create_task_attempt(attempt_b_task, "generation")
        conn = server.db_conn()
        before_task = dict(
            conn.execute(
                "SELECT status,attempt_count,error_code,cancel_requested_at FROM tasks WHERE id=?",
                (task_id,),
            ).fetchone()
        )
        before_attempt_b = dict(
            conn.execute(
                """
                SELECT status,error_code,error_message,assets_json,finished_at
                FROM task_attempts WHERE id=?
                """,
                (attempt_b_id,),
            ).fetchone()
        )
        before_usage = dict(
            conn.execute(
                """
                SELECT status,response_summary,error_code,status_code,actual_point_cost
                FROM usage_log WHERE task_id=? AND api_key_id=?
                ORDER BY id DESC LIMIT 1
                """,
                (task_id, api_key_id),
            ).fetchone()
        )
        conn.close()
        self.assertEqual(before_task["status"], "running")
        self.assertEqual(before_task["attempt_count"], 2)
        self.assertEqual(before_attempt_b["status"], "running")

        server.cancel_task_attempt(
            attempt_a_task,
            attempt_a_id,
            "late cancellation from attempt A",
        )

        conn = server.db_conn()
        after_task = dict(
            conn.execute(
                "SELECT status,attempt_count,error_code,cancel_requested_at FROM tasks WHERE id=?",
                (task_id,),
            ).fetchone()
        )
        attempt_a = conn.execute(
            "SELECT status,error_code FROM task_attempts WHERE id=?",
            (attempt_a_id,),
        ).fetchone()
        after_attempt_b = dict(
            conn.execute(
                """
                SELECT status,error_code,error_message,assets_json,finished_at
                FROM task_attempts WHERE id=?
                """,
                (attempt_b_id,),
            ).fetchone()
        )
        after_usage = dict(
            conn.execute(
                """
                SELECT status,response_summary,error_code,status_code,actual_point_cost
                FROM usage_log WHERE task_id=? AND api_key_id=?
                ORDER BY id DESC LIMIT 1
                """,
                (task_id, api_key_id),
            ).fetchone()
        )
        conn.close()
        self.assertEqual(attempt_a["status"], "expired")
        self.assertEqual(attempt_a["error_code"], "WORKER_LOST")
        self.assertEqual(after_task, before_task)
        self.assertEqual(after_attempt_b, before_attempt_b)
        self.assertEqual(after_usage, before_usage)

    def test_worker_cancel_handles_claim_create_gap_attempt_number_mismatch(self):
        api_key_id, task_id, task, attempt_id = self.seed_running_task_after_claim_create_gap(
            "worker-cancel-gap-key"
        )

        server.cancel_task_attempt(task, attempt_id, "worker cancelled after claim gap")

        conn = server.db_conn()
        task_row = conn.execute(
            "SELECT status,error_code FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        attempt_row = conn.execute(
            "SELECT status,error_code FROM task_attempts WHERE id=?",
            (attempt_id,),
        ).fetchone()
        usage_row = conn.execute(
            """
            SELECT status,error_code,status_code
            FROM usage_log
            WHERE task_id=? AND api_key_id=?
            ORDER BY id DESC LIMIT 1
            """,
            (task_id, api_key_id),
        ).fetchone()
        conn.close()
        self.assertEqual(task_row["status"], "cancelled")
        self.assertEqual(task_row["error_code"], "TASK_CANCELLED")
        self.assertEqual(attempt_row["status"], "cancelled")
        self.assertEqual(attempt_row["error_code"], "TASK_CANCELLED")
        self.assertEqual(usage_row["status"], "cancelled")
        self.assertEqual(usage_row["error_code"], "TASK_CANCELLED")
        self.assertEqual(usage_row["status_code"], 499)

    def test_late_finalize_preserves_attempt_expired_by_worker_recovery(self):
        task_id = self.seed_task(status="running", kind="image", started_at=800.0)
        task = dict(server.fetch_task_row(task_id))
        attempt_id = server.create_task_attempt(task, "generation")
        server.update_task_record(task_id, updated_at=900.0)

        recovered = server.recover_stale_running_tasks(
            now=1000.0,
            stale_after_seconds=60.0,
        )
        self.assertEqual(recovered, 1)
        conn = server.db_conn()
        before = dict(
            conn.execute(
                """
                SELECT status,error_code,error_message,assets_json,finished_at
                FROM task_attempts WHERE id=?
                """,
                (attempt_id,),
            ).fetchone()
        )
        conn.close()
        self.assertEqual(before["status"], "expired")
        self.assertEqual(before["error_code"], "WORKER_LOST")

        server.finalize_task_attempt(
            task,
            attempt_id,
            "generation",
            {
                "account_id": task["account_id"],
                "chat_id": "chat-late-finalize",
                "focus_id": "focus-late-finalize",
                "response_json": {"status": "completed"},
                "assets": ["https://cdn.oreateai.com/static/result/late-finalize.jpg"],
                "response_summary": "completed",
                "status_code": 200,
            },
            "completed",
        )

        conn = server.db_conn()
        after = dict(
            conn.execute(
                """
                SELECT status,error_code,error_message,assets_json,finished_at
                FROM task_attempts WHERE id=?
                """,
                (attempt_id,),
            ).fetchone()
        )
        task_row = conn.execute(
            "SELECT status,error_code,assets_json FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(after, before)
        self.assertEqual(task_row["status"], "expired")
        self.assertEqual(task_row["error_code"], "WORKER_LOST")
        self.assertEqual(json.loads(task_row["assets_json"]), [])

    def test_api_cancel_transaction_finishes_current_running_attempt(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("transactional-cancel-key")
        created = self.client.post(
            "/v1/generate",
            headers={"Authorization": "Bearer transactional-cancel-key"},
            json=self.valid_image_request(),
        )
        self.assertEqual(created.status_code, 202)
        task_id = created.json()["task_id"]
        task = server.claim_next_task()
        self.assertEqual(task["id"], task_id)
        attempt_id = server.create_task_attempt(task, "generation")

        cancelled = self.client.post(
            f"/v1/tasks/{task_id}/cancel",
            headers={"Authorization": "Bearer transactional-cancel-key"},
        )

        self.assertEqual(cancelled.status_code, 200)
        conn = server.db_conn()
        task_row = conn.execute(
            "SELECT status,error_code FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        attempt_row = conn.execute(
            "SELECT status,error_code,assets_json,finished_at FROM task_attempts WHERE id=?",
            (attempt_id,),
        ).fetchone()
        usage_row = conn.execute(
            "SELECT status,error_code,status_code FROM usage_log WHERE task_id=?",
            (task_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(task_row["status"], "cancelled")
        self.assertEqual(task_row["error_code"], "TASK_CANCELLED")
        self.assertEqual(attempt_row["status"], "cancelled")
        self.assertEqual(attempt_row["error_code"], "TASK_CANCELLED")
        self.assertEqual(json.loads(attempt_row["assets_json"]), [])
        self.assertIsNotNone(attempt_row["finished_at"])
        self.assertEqual(usage_row["status"], "cancelled")
        self.assertEqual(usage_row["error_code"], "TASK_CANCELLED")
        self.assertEqual(usage_row["status_code"], 499)

    def test_api_cancel_finishes_latest_running_attempt_after_claim_create_gap(self):
        api_key_id, task_id, _task, attempt_id = self.seed_running_task_after_claim_create_gap(
            "api-cancel-gap-key"
        )

        cancelled = self.client.post(
            f"/v1/tasks/{task_id}/cancel",
            headers={"Authorization": "Bearer api-cancel-gap-key"},
        )

        self.assertEqual(cancelled.status_code, 200)
        conn = server.db_conn()
        task_row = conn.execute(
            "SELECT status,error_code FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        attempt_row = conn.execute(
            "SELECT status,error_code FROM task_attempts WHERE id=?",
            (attempt_id,),
        ).fetchone()
        running_attempts = conn.execute(
            "SELECT COUNT(*) FROM task_attempts WHERE task_id=? AND status='running'",
            (task_id,),
        ).fetchone()[0]
        usage_row = conn.execute(
            """
            SELECT status,error_code,status_code
            FROM usage_log
            WHERE task_id=? AND api_key_id=?
            ORDER BY id DESC LIMIT 1
            """,
            (task_id, api_key_id),
        ).fetchone()
        conn.close()
        self.assertEqual(task_row["status"], "cancelled")
        self.assertEqual(task_row["error_code"], "TASK_CANCELLED")
        self.assertEqual(attempt_row["status"], "cancelled")
        self.assertEqual(attempt_row["error_code"], "TASK_CANCELLED")
        self.assertEqual(running_attempts, 0)
        self.assertEqual(usage_row["status"], "cancelled")
        self.assertEqual(usage_row["error_code"], "TASK_CANCELLED")
        self.assertEqual(usage_row["status_code"], 499)

    def test_api_cancel_rolls_back_task_attempt_and_usage_when_usage_update_fails(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("cancel-rollback-key")
        created = self.client.post(
            "/v1/generate",
            headers={"Authorization": "Bearer cancel-rollback-key"},
            json=self.valid_image_request(),
        )
        self.assertEqual(created.status_code, 202)
        task_id = created.json()["task_id"]
        task = server.claim_next_task()
        self.assertEqual(task["id"], task_id)
        attempt_id = server.create_task_attempt(task, "generation")
        conn = server.db_conn()
        before_task = dict(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
        before_attempt = dict(
            conn.execute("SELECT * FROM task_attempts WHERE id=?", (attempt_id,)).fetchone()
        )
        before_usage = dict(
            conn.execute(
                "SELECT * FROM usage_log WHERE task_id=? ORDER BY id DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        )
        conn.close()

        real_db_conn = server.db_conn

        class FailingUsageConnection:
            def __init__(self):
                self.connection = real_db_conn()
                self.closed = False

            @property
            def in_transaction(self):
                return False if self.closed else self.connection.in_transaction

            def execute(self, sql, parameters=()):
                normalized = " ".join(str(sql).split()).upper()
                is_cancel_usage = normalized.startswith("UPDATE USAGE_LOG SET") and (
                    "STATUS='CANCELLED'" in normalized
                    or (parameters and parameters[0] == "cancelled")
                )
                if is_cancel_usage:
                    self.connection.rollback()
                    self.connection.close()
                    self.closed = True
                    raise RuntimeError("forced usage cancellation failure")
                return self.connection.execute(sql, parameters)

            def commit(self):
                return self.connection.commit()

            def rollback(self):
                if not self.closed:
                    return self.connection.rollback()
                return None

            def close(self):
                if not self.closed:
                    self.connection.close()
                    self.closed = True

        with patch.object(server, "db_conn", side_effect=FailingUsageConnection):
            failure_client = TestClient(server.app, raise_server_exceptions=False)
            try:
                failed = failure_client.post(
                    f"/v1/tasks/{task_id}/cancel",
                    headers={"Authorization": "Bearer cancel-rollback-key"},
                )
            finally:
                failure_client.close()
        self.assertEqual(failed.status_code, 500)

        conn = server.db_conn()
        after_task = dict(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
        after_attempt = dict(
            conn.execute("SELECT * FROM task_attempts WHERE id=?", (attempt_id,)).fetchone()
        )
        after_usage = dict(
            conn.execute(
                "SELECT * FROM usage_log WHERE task_id=? ORDER BY id DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        )
        conn.close()
        self.assertEqual(after_task, before_task)
        self.assertEqual(after_attempt, before_attempt)
        self.assertEqual(after_usage, before_usage)

        retried = self.client.post(
            f"/v1/tasks/{task_id}/cancel",
            headers={"Authorization": "Bearer cancel-rollback-key"},
        )
        self.assertEqual(retried.status_code, 200)
        conn = server.db_conn()
        final_task = conn.execute(
            "SELECT status FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        final_attempt = conn.execute(
            "SELECT status FROM task_attempts WHERE id=?",
            (attempt_id,),
        ).fetchone()
        final_usage = conn.execute(
            "SELECT status FROM usage_log WHERE task_id=? ORDER BY id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(final_task["status"], "cancelled")
        self.assertEqual(final_attempt["status"], "cancelled")
        self.assertEqual(final_usage["status"], "cancelled")

    def test_submitted_task_expires_after_hydration_deadline(self):
        original_cfg = server.CFG
        server.CFG = server.deep_merge(
            original_cfg,
            {
                "gateway": {
                    "submitted_task_expire_seconds": 60,
                    "hydrating_task_expire_seconds": 60,
                }
            },
        )
        try:
            task_id = self.seed_task(
                status="submitted",
                started_at=time.time() - 120,
                response={"status": "submitted", "chat": {"chatId": "chat-expired", "focusId": "focus-expired"}},
                chat_id="chat-expired",
            )
            with patch.object(server, "run_hydration_attempt", side_effect=AssertionError("expired task should not hydrate")):
                processed = self.process_task_queue(limit=1)
        finally:
            server.CFG = original_cfg

        self.assertEqual(processed, 1)
        row = server.fetch_task_row(task_id)
        self.assertIsNotNone(row)
        task = server.task_detail_for_row(row)
        self.assertEqual(task["status"], "expired")
        self.assertEqual(task["error_code"], "TASK_EXPIRED")
        self.assertTrue(task["finished_at"])
        self.assertEqual(task["attempts"][-1]["status"], "expired")

    def test_submitted_task_obeys_hydration_backoff_before_requeue(self):
        original_cfg = server.CFG
        server.CFG = server.deep_merge(
            original_cfg,
            {
                "gateway": {
                    "submitted_task_retry_interval_seconds": 60,
                    "hydrating_task_retry_interval_seconds": 60,
                }
            },
        )
        now = time.time()
        try:
            task_id = self.seed_task(
                status="submitted",
                started_at=now - 10,
                response={"status": "submitted", "chat": {"chatId": "chat-backoff", "focusId": "focus-backoff"}},
                chat_id="chat-backoff",
            )
            conn = server.db_conn()
            conn.execute(
                """
                INSERT INTO task_attempts(
                    task_id, attempt_no, phase, account_id, status, error_code, error_message,
                    request_payload_json, stream_summary_json, hydration_summary_json, assets_json,
                    started_at, finished_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    task_id,
                    1,
                    "hydration",
                    None,
                    "submitted",
                    "",
                    "",
                    json.dumps(self.valid_video_request()),
                    None,
                    json.dumps({"status": "submitted"}),
                    json.dumps([]),
                    now - 5,
                    now - 5,
                ),
            )
            conn.commit()
            conn.close()
            server.update_task_record(task_id, next_attempt_at=now + 60)

            with patch.object(server, "run_hydration_attempt", side_effect=AssertionError("backoff task should not hydrate")):
                processed = self.process_task_queue(limit=1)
        finally:
            server.CFG = original_cfg

        self.assertEqual(processed, 0)
        row = server.fetch_task_row(task_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "submitted")
        task = server.task_detail_for_row(row)
        self.assertEqual(len(task["attempts"]), 1)

    def test_startup_recovery_expires_stale_running_task_and_attempt(self):
        account_id = self.seed_account_with_capabilities()
        api_key_id = self.seed_api_key("recovery-key")
        task_id = server.save_task(
            account_id,
            "image",
            "recover me",
            self.valid_image_request(),
            {"status": "running"},
            status="running",
            api_key_id=api_key_id,
            request_id="req-recovery",
            model_name="Google Nano Banana 2",
            resolution="4K",
            ratio="16:9",
            started_at=100.0,
        )
        conn = server.db_conn()
        conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (100.0, task_id))
        conn.execute(
            """
            INSERT INTO task_attempts(task_id,attempt_no,phase,account_id,status,started_at)
            VALUES(?,?,?,?,?,?)
            """,
            (task_id, 1, "generation", account_id, "running", 100.0),
        )
        conn.commit()
        conn.close()

        recovered = server.recover_stale_running_tasks(now=1000.0, stale_after_seconds=60.0)

        self.assertEqual(recovered, 1)
        conn = server.db_conn()
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        attempt = conn.execute("SELECT * FROM task_attempts WHERE task_id=?", (task_id,)).fetchone()
        conn.close()
        self.assertEqual(task["status"], "expired")
        self.assertEqual(task["error_code"], "WORKER_LOST")
        self.assertEqual(task["finished_at"], 1000.0)
        self.assertEqual(attempt["status"], "expired")
        self.assertEqual(attempt["error_code"], "WORKER_LOST")

    def test_readyz_fails_when_background_worker_is_enabled_but_not_alive(self):
        self.seed_account_with_capabilities()
        original = server.CFG["gateway"].get("enable_background_worker")
        original_thread = server.TASK_WORKER_THREAD
        server.CFG["gateway"]["enable_background_worker"] = True
        server.TASK_WORKER_THREAD = None
        try:
            response = self.client.get("/readyz")
        finally:
            server.CFG["gateway"]["enable_background_worker"] = original
            server.TASK_WORKER_THREAD = original_thread

        self.assertEqual(response.status_code, 503)
        self.assertIn("task worker", response.json()["detail"])

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

    def test_stream_generation_uses_web_video_headers(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def iter_lines(self, decode_unicode=True):
                return iter(['data: {"event":"end","data":{}}'])

            def close(self):
                return None

        class FakeSession:
            def __init__(self):
                self.cookies = {"OUID": "ouid-secret", "__bid_n": "bid-secret"}
                self.last_headers = {}
                self.last_json = {}

            def post(self, url, **kwargs):
                self.last_headers = kwargs["headers"]
                self.last_json = kwargs["json"]
                return FakeResponse()

        fake = FakeSession()
        client = server.OreateClient()
        client.stream_generation(
            fake,
            chat_id="chat-video",
            focus_id="focus-video",
            chat_type="aiVideo",
            prompt="hello",
            video_config={"modelName": "Seedance 1.5 Pro", "scene": "text_or_image"},
            jt="test-jt",
        )

        self.assertEqual(fake.last_headers["accept"], "text/event-stream")
        self.assertEqual(fake.last_headers["content-type"], "application/json")
        self.assertEqual(fake.last_headers["Client-Type"], "pc")
        self.assertTrue(fake.last_headers["referer"].endswith("/home/vertical/aiVideo/zh"))
        self.assertEqual(fake.last_json["chatType"], "aiVideo")
        self.assertIn("videoConfig", fake.last_json)

    def test_video_stream_read_timeout_after_ping_is_submitted(self):
        class FakeResponse:
            def __init__(self):
                self.closed = False

            def raise_for_status(self):
                return None

            def iter_lines(self, decode_unicode=True):
                yield 'data: {"event":"start","data":{}}'
                yield 'data: {"event":"ping","data":{}}'
                raise server.requests.exceptions.ReadTimeout("read timed out")

            def close(self):
                self.closed = True

        class FakeSession:
            def __init__(self):
                self.cookies = {"OUID": "ouid-secret", "__bid_n": "bid-secret"}
                self.response = FakeResponse()

            def post(self, url, **kwargs):
                return self.response

        fake = FakeSession()
        client = server.OreateClient()
        result = client.stream_generation(
            fake,
            chat_id="chat-video",
            focus_id="focus-video",
            chat_type="aiVideo",
            prompt="hello",
            video_config={"modelName": "Seedance 1.5 Pro", "scene": "text_or_image"},
            jt="test-jt",
        )

        self.assertIsNone(result["error"])
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["completion_reason"], "read_timeout")
        self.assertEqual([event["event"] for event in result["events"]], ["start", "ping"])
        self.assertTrue(fake.response.closed)

    def test_video_stream_empty_eof_is_not_treated_as_submitted(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def iter_lines(self, decode_unicode=True):
                return iter([])

            def close(self):
                return None

        class FakeSession:
            def __init__(self):
                self.cookies = {"OUID": "ouid-secret", "__bid_n": "bid-secret"}

            def post(self, url, **kwargs):
                return FakeResponse()

        client = server.OreateClient()
        result = client.stream_generation(
            FakeSession(),
            chat_id="chat-video",
            focus_id="focus-video",
            chat_type="aiVideo",
            prompt="hello",
            video_config={"modelName": "Seedance 1.5 Pro", "scene": "text_or_image"},
            jt="test-jt",
        )

        self.assertEqual(result["events"], [])
        self.assertEqual(result["completion_reason"], "eof")
        self.assertEqual(result["status"], "streamed")

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

    def test_stream_generation_retries_banti_helper_until_bid_cookie_is_available(self):
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
            side_effect=[
                {"jt": "fallback-jt", "cookies": {}},
                {"jt": "helper-jt", "cookies": {"__bid_n": "helper-bid"}},
            ],
        ) as helper:
            client.stream_generation(
                fake,
                chat_id="chat-img",
                focus_id="focus-img",
                chat_type="aiImage",
                prompt="hello",
                image_config={"modelName": "Google Nano Banana 2", "ratio": "16:9", "resolution": "4K"},
            )

        self.assertEqual(helper.call_count, 2)
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

    def test_video_config_clears_empty_capability_options_like_web(self):
        config = server.build_video_config(
            {
                "model_name": "Seedance Auto",
                "ratio": "16:9",
                "resolution": "480",
                "duration": 5,
                "scene_id": "text_or_image",
            },
            {
                "name": "Seedance Auto",
                "ratios": [],
                "resolutions": [],
                "durations": [],
                "supports_audio": False,
            },
        )

        self.assertEqual(config["ratio"], "")
        self.assertEqual(config["resolution"], "")
        self.assertNotIn("duration", config)
        self.assertFalse(config["isAudio"])
        self.assertEqual(config["aiType"], 0)

    def test_video_motion_config_omits_duration_and_ratio_like_web_restrictions(self):
        config = server.build_video_config(
            {
                "model_name": "Kling 2.6",
                "ratio": "1:1",
                "resolution": "720",
                "duration": 5,
                "scene_id": "motion",
                "motion_duration": 3,
                "is_audio": True,
                "motion_video": self.uploaded_video("motion.mp4", "uploads/motion.mp4", duration=3),
                "character_image": self.uploaded_image("character.png", "uploads/character.png"),
            },
            {
                "name": "Kling 2.6",
                "ratios": ["1:1"],
                "resolutions": ["720"],
                "durations": [5, 10],
                "supports_audio": True,
                "point_cost_motion": [
                    {"motDuration": 3, "resolution": "720", "point": 15, "aiType": 14172}
                ],
            },
        )

        self.assertEqual(config["scene"], "motion")
        self.assertEqual(config["ratio"], "")
        self.assertNotIn("duration", config)
        self.assertFalse(config["isAudio"])
        self.assertEqual(config["resolution"], "720")
        self.assertEqual(config["aiType"], 14172)
        self.assertEqual(config["motion"]["motDuration"], 3)

    def test_video_cost_matching_treats_missing_audio_as_false(self):
        caps = {
            "video": {
                "models": [
                    {
                        "name": "Seedance 2.0 Mini",
                        "point_cost_image": [
                            {"duration": 5, "resolution": "480", "audio": False, "point": 20}
                        ],
                    }
                ]
            }
        }

        cost = server.estimate_point_cost(
            "video",
            {
                "model_name": "Seedance 2.0 Mini",
                "scene_id": "text_or_image",
                "duration": 5,
                "resolution": "480",
            },
            caps,
        )

        self.assertEqual(cost, 20)

    def test_video_duration_capability_accepts_web_value_objects(self):
        models = server.normalize_video_models(
            {
                "models": {
                    "data": {
                        "models": [
                            {
                                "modelName": "Seedance Live Shape",
                                "duration": [{"value": 5}, {"value": 10}],
                                "videoResolution": ["480"],
                                "videoSize": [{"ratio": "16:9"}],
                            }
                        ]
                    }
                }
            }
        )

        self.assertEqual(models[0]["durations"], [5, 10])

    def test_upload_attachment_is_normalized_like_web_nke(self):
        attachment = server.normalize_upload_attachment(
            {
                "fileName": "first",
                "fileExt": "png",
                "originSize": 1234,
                "object": "uploads/first.png",
                "videoDurationSec": 0,
            }
        )

        self.assertEqual(attachment["bos_url"], "uploads/first.png")
        self.assertEqual(attachment["bosUrl"], "uploads/first.png")
        self.assertEqual(attachment["doc_title"], "first")
        self.assertEqual(attachment["doc_type"], "png")
        self.assertEqual(attachment["size"], 1234)
        self.assertEqual(attachment["flag"], "upload")
        self.assertEqual(attachment["type"], "file")
        self.assertEqual(attachment["status"], 1)
        self.assertNotIn("videoDurationSec", attachment)

    def test_reference_video_rejects_attachment_without_object_path(self):
        with self.assertRaises(server.GatewayAPIError) as raised:
            server.build_video_config(
                {
                    "model_name": "Seedance 2.0 Mini",
                    "ratio": "16:9",
                    "resolution": "480",
                    "duration": 5,
                    "scene_id": "reference",
                    "reference_images": [{"fileName": "ref", "fileExt": "png", "originSize": 1234}],
                },
                {"name": "Seedance 2.0 Mini", "ai_type": 14198},
            )

        self.assertEqual(raised.exception.code, "MISSING_VIDEO_ATTACHMENT")
        self.assertEqual(raised.exception.details["field"], "reference_images")

    def test_video_frame_based_requires_both_frames(self):
        with self.assertRaises(server.GatewayAPIError) as raised:
            server.build_video_config(
                {
                    "model_name": "Seedance 2.0 Mini",
                    "ratio": "16:9",
                    "resolution": "480",
                    "duration": 5,
                    "scene_id": "frame_based",
                    "first_frame": self.uploaded_image(),
                },
                {"name": "Seedance 2.0 Mini", "ai_type": 14198},
            )

        self.assertEqual(raised.exception.code, "MISSING_VIDEO_ATTACHMENT")

    def test_video_frame_based_config_uses_uploaded_object_paths_and_attachments(self):
        options = {
            "model_name": "Seedance 2.0 Mini",
            "ratio": "16:9",
            "resolution": "480",
            "duration": 5,
            "scene_id": "frame_based",
            "first_frame": self.uploaded_image("first.png", "uploads/first.png"),
            "last_frame": self.uploaded_image("last.png", "uploads/last.png"),
        }

        config = server.build_video_config(options, {"name": "Seedance 2.0 Mini", "ai_type": 14198})
        attachments = server.build_video_message_attachments(options)

        self.assertEqual(config["frameBased"]["firstFrame"], "uploads/first.png")
        self.assertEqual(config["frameBased"]["lastFrame"], "uploads/last.png")
        self.assertEqual([a["bos_url"] for a in attachments], ["uploads/first.png", "uploads/last.png"])

    def test_video_motion_requires_character_and_motion_video(self):
        with self.assertRaises(server.GatewayAPIError) as raised:
            server.build_video_config(
                {
                    "model_name": "Seedance 2.0 Mini",
                    "ratio": "16:9",
                    "resolution": "480",
                    "duration": 5,
                    "scene_id": "motion",
                    "motion_video": self.uploaded_video(),
                },
                {"name": "Seedance 2.0 Mini", "ai_type": 14198},
            )

        self.assertEqual(raised.exception.code, "MISSING_VIDEO_ATTACHMENT")

    def test_v1_generate_reference_video_passes_scene_config_and_message_attachments(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("reference-key")
        image = self.uploaded_image("ref.png", "uploads/ref.png")
        video = self.uploaded_video("ref.mp4", "uploads/ref.mp4", duration=4)
        original_cfg = server.CFG
        server.CFG = server.deep_merge(
            original_cfg,
            {"gateway": {"scene_policies": {"reference": {"enabled": True, "experimental": True, "verification_status": "unit_tested"}}}},
        )
        try:
            with (
                patch.object(server.CLIENT, "session_from_account", return_value=object()),
                patch.object(server.CLIENT, "create_chat_session", return_value={"chatId": "chat-ref", "focusId": "focus-ref"}),
                patch.object(server.CLIENT, "stream_generation", return_value={"events": [{"event": "end"}], "error": None}) as stream_generation,
                patch.object(server.CLIENT, "hydrate_generation_result", return_value={"raw": {}, "assets": ["https://cdn.oreateai.com/static/result/ref.mp4"]}),
            ):
                response = self.client.post(
                    "/v1/generate",
                    headers={"Authorization": "Bearer reference-key"},
                    json={
                        "kind": "video",
                        "prompt": "hello",
                        "model_name": "Seedance 2.0 Mini",
                        "resolution": "480",
                        "ratio": "16:9",
                        "duration": 5,
                        "scene_id": "reference",
                        "reference_images": [image],
                        "reference_videos": [video],
                        "ref_duration": "2-5",
                        "ref_total_duration": 4,
                        "keep_original_sound": True,
                        "sync_wait_seconds": 1,
                    },
                )
        finally:
            server.CFG = original_cfg

        self.assertEqual(response.status_code, 200)
        kwargs = stream_generation.call_args.kwargs
        self.assertEqual(kwargs["video_config"]["reference"]["referenceImages"], ["uploads/ref.png"])
        self.assertEqual(kwargs["video_config"]["reference"]["referenceVideos"], ["uploads/ref.mp4"])
        self.assertEqual(kwargs["video_config"]["reference"]["refTotalDuration"], 4)
        self.assertTrue(kwargs["video_config"]["reference"]["keepOriginalSound"])
        self.assertEqual([a["bos_url"] for a in kwargs["attachments"]], ["uploads/ref.png", "uploads/ref.mp4"])

    def test_upload_endpoint_uses_oreate_bos_upload_protocol(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("upload-key")

        with patch.object(
            server.CLIENT,
            "upload_file_bytes",
            return_value={
                "fileName": "sample",
                "fileExt": "png",
                "originSize": 4,
                "object": "uploads/sample.png",
                "status": "completed",
            },
        ) as upload_file:
            response = self.client.post(
                "/v1/uploads",
                headers={"Authorization": "Bearer upload-key"},
                files={"file": ("sample.png", b"data", "image/png")},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["attachment"]["object"], "uploads/sample.png")
        self.assertEqual(payload["message_attachment"]["bos_url"], "uploads/sample.png")
        upload_file.assert_called_once()

    def test_concurrent_uploads_share_daily_request_admission(self):
        self.seed_account_with_capabilities()
        api_key_id = self.seed_api_key("concurrent-upload-key", daily_request_limit=1)
        original_check_daily_quota = server.check_daily_quota
        quota_checked = threading.Barrier(2)

        def synchronized_check_daily_quota(*args, **kwargs):
            original_check_daily_quota(*args, **kwargs)
            try:
                quota_checked.wait(timeout=0.5)
            except threading.BrokenBarrierError:
                pass

        attachment = {
            "fileName": "sample",
            "fileExt": "png",
            "originSize": 8,
            "object": "uploads/sample.png",
            "status": "completed",
        }
        files = [
            ("first.png", b"\x89PNG\r\n\x1a\n", "image/png"),
            ("second.png", b"\x89PNG\r\n\x1a\n", "image/png"),
        ]
        with (
            patch.object(server, "check_daily_quota", side_effect=synchronized_check_daily_quota),
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "upload_file_bytes", return_value=attachment) as upload_file,
        ):
            responses = self.post_upload_requests_concurrently("concurrent-upload-key", files)

        self.assertEqual(sorted(response.status_code for response in responses), [200, 429])
        rejected = next(response for response in responses if response.status_code == 429)
        self.assertEqual(rejected.json()["error"]["code"], "DAILY_REQUEST_LIMIT_EXCEEDED")
        upload_file.assert_called_once()

        conn = server.db_conn()
        usage_count = conn.execute(
            "SELECT COUNT(*) FROM usage_log WHERE api_key_id=?",
            (api_key_id,),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(usage_count, 1)

    def test_upload_rejects_file_larger_than_configured_limit_before_upstream(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("upload-size-key")
        original = server.CFG["gateway"].get("upload_max_bytes")
        server.CFG["gateway"]["upload_max_bytes"] = 3
        try:
            with patch.object(server.CLIENT, "session_from_account") as session_from_account:
                response = self.client.post(
                    "/v1/uploads",
                    headers={"Authorization": "Bearer upload-size-key"},
                    files={"file": ("sample.png", b"data", "image/png")},
                )
        finally:
            if original is None:
                server.CFG["gateway"].pop("upload_max_bytes", None)
            else:
                server.CFG["gateway"]["upload_max_bytes"] = original

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"]["code"], "UPLOAD_TOO_LARGE")
        session_from_account.assert_not_called()

    def test_upload_rejects_non_media_extension_before_upstream(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("upload-type-key")

        with patch.object(server.CLIENT, "session_from_account") as session_from_account:
            response = self.client.post(
                "/v1/uploads",
                headers={"Authorization": "Bearer upload-type-key"},
                files={"file": ("payload.exe", b"data", "application/octet-stream")},
            )

        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.json()["error"]["code"], "UNSUPPORTED_UPLOAD_TYPE")
        session_from_account.assert_not_called()

    def test_upload_rejects_exhausted_daily_quota_before_reading_body(self):
        account_id = self.seed_account_with_capabilities()
        api_key_id = self.seed_api_key("upload-quota-before-read-key", daily_request_limit=1)
        conn = server.db_conn()
        conn.execute(
            "INSERT INTO usage_log(api_key_id,kind,account_id,prompt,status,response_summary,created_at) VALUES(?,?,?,?,?,?,?)",
            (api_key_id, "upload", account_id, "previous upload", "completed", "ok", time.time()),
        )
        conn.commit()
        conn.close()

        async def fail_if_read(*args, **kwargs):
            raise AssertionError("UploadFile.read should not run when quota is already exhausted")

        with patch.object(server.UploadFile, "read", side_effect=fail_if_read):
            response = self.client.post(
                "/v1/uploads",
                headers={"Authorization": "Bearer upload-quota-before-read-key"},
                files={"file": ("sample.png", b"data", "image/png")},
            )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"]["code"], "DAILY_REQUEST_LIMIT_EXCEEDED")

    def test_upload_obeys_api_key_rate_limit(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("upload-rate-key", rate_limit_per_minute=1)
        attachment = {
            "fileName": "sample",
            "fileExt": "png",
            "originSize": 4,
            "object": "uploads/sample.png",
            "status": "completed",
        }
        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "upload_file_bytes", return_value=attachment) as upload_file,
        ):
            first = self.client.post(
                "/v1/uploads",
                headers={"Authorization": "Bearer upload-rate-key"},
                files={"file": ("sample.png", b"data", "image/png")},
            )
            second = self.client.post(
                "/v1/uploads",
                headers={"Authorization": "Bearer upload-rate-key"},
                files={"file": ("sample.png", b"data", "image/png")},
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["error"]["code"], "RATE_LIMITED")
        upload_file.assert_called_once()

    def test_upload_failure_returns_sanitized_error_message(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("upload-error-key")
        secret = "sessionkey-super-secret"
        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "upload_file_bytes", side_effect=RuntimeError(f"upstream leaked {secret}")),
        ):
            response = self.client.post(
                "/v1/uploads",
                headers={"Authorization": "Bearer upload-error-key"},
                files={"file": ("sample.png", b"data", "image/png")},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "UPLOAD_FAILED")
        self.assertNotIn(secret, response.text)

    def test_upload_file_bytes_accepts_web_keylist_object(self):
        class FakeResponse:
            def __init__(self, body=None, headers=None):
                self._body = body or {}
                self.headers = headers or {}

            def raise_for_status(self):
                return None

            def json(self):
                return self._body

        class FakeSession:
            def __init__(self):
                self.posts = []

            def post(self, url, **kwargs):
                self.posts.append((url, kwargs))
                if url.endswith("/oreate/convert/submit"):
                    return FakeResponse({"docId": "doc-sample", "parseInfo": {"ok": True}})
                return FakeResponse(
                    {
                        "KeyList": {
                            "0": {
                                "bucket": "bucket-a",
                                "objectPath": "uploads/sample.png",
                                "sessionkey": "token-a",
                            }
                        }
                    }
                )

        client = server.OreateClient()
        with (
            patch.object(server.requests, "post", return_value=FakeResponse(headers={"Location": "https://upload.example/session"})) as init_upload,
            patch.object(server.requests, "put", return_value=FakeResponse()) as put_upload,
        ):
            attachment = client.upload_file_bytes(FakeSession(), "sample.png", b"data", "image/png")

        self.assertEqual(attachment["object"], "uploads/sample.png")
        self.assertEqual(attachment["docId"], "doc-sample")
        self.assertEqual(attachment["parseInfo"], {"ok": True})
        init_upload.assert_called_once()
        put_upload.assert_called_once()

    def test_parse_mp4_video_metadata_extracts_duration_and_dimensions(self):
        metadata = server.parse_mp4_video_metadata(self.sample_mp4_bytes(duration_sec=3, width=320, height=240))

        self.assertEqual(metadata["videoDurationSec"], 3.0)
        self.assertEqual(metadata["videoWidth"], 320)
        self.assertEqual(metadata["videoHeight"], 240)

    def test_video_upload_skips_convert_submit_after_bos_upload(self):
        class FakeResponse:
            def __init__(self, body=None, headers=None):
                self._body = body or {}
                self.headers = headers or {}

            def raise_for_status(self):
                return None

            def json(self):
                return self._body

        class FakeSession:
            def __init__(self):
                self.posts = []

            def post(self, url, **kwargs):
                self.posts.append((url, kwargs))
                if url.endswith("/oreate/convert/submit"):
                    return FakeResponse({"data": {"docId": "doc-video", "parseInfo": {"type": "media"}}})
                return FakeResponse(
                    {
                        "KeyList": {
                            "0": {
                                "bucket": "bucket-a",
                                "objectPath": "uploads/ref.mp4",
                                "sessionkey": "token-a",
                            }
                        }
                    }
                )

        fake_session = FakeSession()
        client = server.OreateClient()
        with (
            patch.object(server.requests, "post", return_value=FakeResponse(headers={"Location": "https://upload.example/session"})),
            patch.object(server.requests, "put", return_value=FakeResponse()),
        ):
            attachment = client.upload_file_bytes(fake_session, "ref.mp4", self.sample_mp4_bytes(), "video/mp4")

        token_payload = fake_session.posts[0][1]["json"]
        self.assertEqual(token_payload["source"], "aiImage")
        self.assertEqual(len(fake_session.posts), 1)
        self.assertEqual(attachment["object"], "uploads/ref.mp4")
        self.assertEqual(attachment["videoDurationSec"], 3.0)
        self.assertEqual(attachment["videoWidth"], 320)
        self.assertEqual(attachment["videoHeight"], 240)
        self.assertNotIn("docId", attachment)
        self.assertNotIn("parseInfo", attachment)

    def test_image_upload_uses_convert_submit(self):
        class FakeResponse:
            def __init__(self, body=None, headers=None):
                self._body = body or {}
                self.headers = headers or {}

            def raise_for_status(self):
                return None

            def json(self):
                return self._body

        class FakeSession:
            def __init__(self):
                self.posts = []

            def post(self, url, **kwargs):
                self.posts.append((url, kwargs))
                if url.endswith("/oreate/convert/submit"):
                    return FakeResponse({"data": {"docId": "doc-image", "parseInfo": {"type": "image"}}})
                return FakeResponse(
                    {
                        "KeyList": {
                            "0": {
                                "bucket": "bucket-a",
                                "objectPath": "uploads/ref.png",
                                "sessionkey": "token-a",
                            }
                        }
                    }
                )

        fake_session = FakeSession()
        client = server.OreateClient()
        with (
            patch.object(server.requests, "post", return_value=FakeResponse(headers={"Location": "https://upload.example/session"})),
            patch.object(server.requests, "put", return_value=FakeResponse()),
        ):
            attachment = client.upload_file_bytes(fake_session, "ref.png", b"data", "image/png")

        token_payload = fake_session.posts[0][1]["json"]
        convert_payload = fake_session.posts[1][1]["json"]
        self.assertEqual(token_payload["source"], "aiImage")
        self.assertEqual(convert_payload["object"], "uploads/ref.png")
        self.assertEqual(convert_payload["fileName"], "ref.png")
        self.assertEqual(attachment["docId"], "doc-image")
        self.assertEqual(attachment["parseInfo"], {"type": "image"})

    def test_non_media_upload_skips_ai_image_source_and_convert_submit(self):
        class FakeResponse:
            def __init__(self, body=None, headers=None):
                self._body = body or {}
                self.headers = headers or {}

            def raise_for_status(self):
                return None

            def json(self):
                return self._body

        class FakeSession:
            def __init__(self):
                self.posts = []

            def post(self, url, **kwargs):
                self.posts.append((url, kwargs))
                return FakeResponse(
                    {
                        "KeyList": {
                            "0": {
                                "bucket": "bucket-a",
                                "objectPath": "uploads/readme.txt",
                                "sessionkey": "token-a",
                            }
                        }
                    }
                )

        fake_session = FakeSession()
        client = server.OreateClient()
        with (
            patch.object(server.requests, "post", return_value=FakeResponse(headers={"Location": "https://upload.example/session"})),
            patch.object(server.requests, "put", return_value=FakeResponse()),
        ):
            attachment = client.upload_file_bytes(fake_session, "readme.txt", b"data", "text/plain")

        token_payload = fake_session.posts[0][1]["json"]
        self.assertNotIn("source", token_payload)
        self.assertEqual(len(fake_session.posts), 1)
        self.assertEqual(attachment["object"], "uploads/readme.txt")

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

    def test_success_history_status_is_not_classified_as_error(self):
        body = {
            "status": {"code": 0, "msg": "success", "errMsg": "success"},
            "data": {"messageList": [{"role": "assistant", "content": "generating video"}]},
        }

        self.assertIsNone(server.classify_history_error(body, ignored_codes=["110012"]))

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

    def test_hydration_ignores_user_uploaded_assets(self):
        body = {
            "status": {"code": 0},
            "data": {
                "messageList": [
                    {
                        "role": "user",
                        "content": "source image",
                        "attachments": [
                            {"bosUrl": "https://cdn.oreateai.com/aiimage/upload/source.png"},
                        ],
                    },
                    {
                        "role": "assistant",
                        "content": "generating video",
                    },
                ]
            },
        }

        assets = server.extract_generation_assets(body)

        self.assertEqual(assets, [])

    def test_video_hydration_polling_extracts_video_html_src(self):
        class FakeResponse:
            def __init__(self, body):
                self.body = body

            def raise_for_status(self):
                return None

            def json(self):
                return self.body

        class FakeSession:
            def __init__(self):
                self.calls = []
                self.responses = [
                    {
                        "status": {"code": 0},
                        "data": {"messageList": [{"role": "assistant", "content": "generating video", "status": 1}]},
                    },
                    {
                        "status": {"code": 0},
                        "data": {
                            "messageList": [
                                {
                                    "role": "assistant",
                                    "content": '<video controls src="https://cdn.oreateai.com/aivideo/videodownload/1899992928.mp4"></video>',
                                }
                            ]
                        },
                    },
                ]

            def get(self, url, **kwargs):
                self.calls.append({"url": url, "kwargs": kwargs})
                return FakeResponse(self.responses.pop(0))

        client = server.OreateClient()
        with patch.object(server.time, "sleep", return_value=None):
            result = client.hydrate_generation_result_until_assets(
                FakeSession(),
                "chat-video",
                timeout_sec=5,
                poll_interval_sec=1,
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["assets"], ["https://cdn.oreateai.com/aivideo/videodownload/1899992928.mp4"])

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
                json=self.valid_image_request(sync_wait_seconds=1),
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["response"]["chat"]["chatId"], "chat-web")
        self.assertEqual(payload["assets"], ["https://cdn.oreateai.com/static/result/x.jpg"])
        create_session.assert_called_once()
        stream_generation.assert_called_once()
        hydrate.assert_called_once()

    def test_v1_generate_video_polls_history_after_ping_only_stream(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("video-poll-key")

        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "create_chat_session", return_value={"chatId": "chat-video", "focusId": "focus-video"}),
            patch.object(
                server.CLIENT,
                "stream_generation",
                return_value={
                    "events": [{"event": "start"}, {"event": "ping"}],
                    "error": None,
                    "status": "submitted",
                    "completion_reason": "read_timeout",
                },
            ),
            patch.object(server.CLIENT, "hydrate_generation_result", return_value={"raw": {}, "assets": []}) as hydrate_once,
            patch.object(
                server.CLIENT,
                "hydrate_generation_result_until_assets",
                create=True,
                return_value={
                    "raw": {
                        "status": {"code": 0},
                        "data": {"messageList": [{"content": '<video src="https://cdn.oreateai.com/aivideo/videodownload/1899992928.mp4">'}]},
                    },
                    "assets": ["https://cdn.oreateai.com/aivideo/videodownload/1899992928.mp4"],
                    "status": "completed",
                    "attempts": 2,
                },
            ) as hydrate_until,
        ):
            response = self.client.post(
                "/v1/generate",
                headers={"Authorization": "Bearer video-poll-key"},
                json=self.valid_video_request(sync_wait_seconds=1),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["assets"], ["https://cdn.oreateai.com/aivideo/videodownload/1899992928.mp4"])
        hydrate_once.assert_not_called()
        hydrate_until.assert_called_once()

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

        self.assertEqual(response.status_code, 202)
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
        self.assertEqual(row["status_code"], 202)

    def test_generate_records_balance_snapshots_and_actual_cost(self):
        account_id = self.seed_account_with_capabilities()
        key_id = self.seed_api_key("actual-cost-key")

        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "fetch_account_point_detail", side_effect=[
                {"data": {"daily": 27, "bonus": 100, "restPoint": 127}},
                {"data": {"daily": 24, "bonus": 100, "restPoint": 124}},
            ]),
            patch.object(server.CLIENT, "create_chat_session", return_value={"chatId": "chat-cost"} ),
            patch.object(server.CLIENT, "stream_generation", return_value={"events": [{"event": "end"}], "error": None}),
            patch.object(server.CLIENT, "hydrate_generation_result", return_value={"raw": {}, "assets": ["https://cdn.oreateai.com/static/result/cost.jpg"]}),
        ):
            response = self.client.post(
                "/v1/generate",
                headers={"Authorization": "Bearer actual-cost-key"},
                json=self.valid_image_request(sync_wait_seconds=1),
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["task"]["actual_point_cost"], 3)
        self.assertEqual(payload["task"]["balance_before_rest_point"], 127)
        self.assertEqual(payload["task"]["balance_after_rest_point"], 124)
        self.assertEqual(payload["task"]["balance_before_daily_point"], 27)
        self.assertEqual(payload["task"]["balance_after_daily_point"], 24)
        self.assertEqual(payload["task"]["balance_before_bonus_point"], 100)
        self.assertEqual(payload["task"]["balance_after_bonus_point"], 100)

        conn = server.db_conn()
        task_row = conn.execute(
            "SELECT actual_point_cost,balance_before_json,balance_after_json,balance_before_rest_point,balance_after_rest_point FROM tasks ORDER BY id DESC LIMIT 1"
        ).fetchone()
        usage_row = conn.execute(
            "SELECT actual_point_cost,estimated_point_cost,status_code FROM usage_log WHERE api_key_id=? ORDER BY id DESC LIMIT 1",
            (key_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(task_row["actual_point_cost"], 3)
        self.assertEqual(task_row["balance_before_rest_point"], 127)
        self.assertEqual(task_row["balance_after_rest_point"], 124)
        self.assertEqual(json.loads(task_row["balance_before_json"])["rest_point"], 127)
        self.assertEqual(json.loads(task_row["balance_after_json"])["rest_point"], 124)
        self.assertEqual(usage_row["actual_point_cost"], 3)
        self.assertEqual(usage_row["estimated_point_cost"], 12)
        self.assertEqual(usage_row["status_code"], 200)

    def test_failed_generation_records_actual_cost_for_failed_upstream_error(self):
        self.seed_account_with_capabilities()
        key_id = self.seed_api_key("failed-actual-cost-key")
        client = TestClient(server.app, raise_server_exceptions=False)

        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(
                server.CLIENT,
                "fetch_account_point_detail",
                side_effect=[
                    {"data": {"daily": 0, "bonus": 100, "restPoint": 100}},
                    {"data": {"daily": 0, "bonus": 90, "restPoint": 90}},
                ],
            ),
            patch.object(server.CLIENT, "create_chat_session", return_value={"chatId": "chat-failed-cost"}),
            patch.object(
                server.CLIENT,
                "stream_generation",
                return_value={
                    "events": [{"event": "error"}],
                    "error": {"code": "100003", "message": "point deducted on failure"},
                    "status": "failed",
                },
            ),
        ):
            response = client.post(
                "/v1/generate",
                headers={"Authorization": "Bearer failed-actual-cost-key"},
                json=self.valid_image_request(sync_wait_seconds=1),
            )

        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "100003")
        task_id = payload["error"]["details"]["task_id"]

        conn = server.db_conn()
        task_row = conn.execute(
            """
            SELECT status,error_code,actual_point_cost,balance_before_rest_point,balance_after_rest_point
            FROM tasks
            WHERE id=?
            """,
            (task_id,),
        ).fetchone()
        usage_row = conn.execute(
            "SELECT status,error_code,actual_point_cost,status_code FROM usage_log WHERE api_key_id=? ORDER BY id DESC LIMIT 1",
            (key_id,),
        ).fetchone()
        conn.close()

        self.assertEqual(task_row["status"], "failed")
        self.assertEqual(task_row["error_code"], "100003")
        self.assertEqual(task_row["actual_point_cost"], 10)
        self.assertEqual(task_row["balance_before_rest_point"], 100)
        self.assertEqual(task_row["balance_after_rest_point"], 90)
        self.assertEqual(usage_row["status"], "failed")
        self.assertEqual(usage_row["error_code"], "100003")
        self.assertEqual(usage_row["actual_point_cost"], 10)
        self.assertEqual(usage_row["status_code"], 503)

    def test_idempotency_key_replays_same_response_without_second_task(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("idem-key")
        request = self.valid_image_request(sync_wait_seconds=1)

        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "create_chat_session", return_value={"chatId": "chat-idem"}) as create_chat,
            patch.object(server.CLIENT, "stream_generation", return_value={"events": [{"event": "end"}], "error": None}),
            patch.object(server.CLIENT, "hydrate_generation_result", return_value={"raw": {}, "assets": []}),
        ):
            first = self.client.post(
                "/v1/generate",
                headers={"Authorization": "Bearer idem-key", "Idempotency-Key": "same-1"},
                json=request,
            )
            second = self.client.post(
                "/v1/generate",
                headers={"Authorization": "Bearer idem-key", "Idempotency-Key": "same-1"},
                json=request,
            )

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertTrue(second.json()["idempotent_replay"])
        self.assertEqual(first.json()["task_id"], second.json()["task_id"])
        self.assertEqual(create_chat.call_count, 1)

    def test_idempotency_key_conflict_rejects_different_body(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("idem-conflict-key")
        changed = self.valid_image_request(sync_wait_seconds=1)
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
                json=self.valid_image_request(sync_wait_seconds=1),
            )
            conflict = self.client.post(
                "/v1/generate",
                headers={"Authorization": "Bearer idem-conflict-key", "Idempotency-Key": "same-2"},
                json=changed,
            )

        self.assertEqual(first.status_code, 202)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["code"], "IDEMPOTENCY_KEY_CONFLICT")

    def test_pending_idempotency_key_rejects_duplicate_without_queuing_task(self):
        api_key_id = self.seed_api_key("idem-pending-key")
        request = self.valid_image_request()
        request_hash = server.request_hash_for_generation(server.GatewayGenerateIn(**request))
        reservation = server.reserve_idempotency_record(
            api_key_id,
            "pending-request",
            request_hash,
        )
        self.assertEqual(reservation["state"], "reserved")

        response = self.client.post(
            "/v1/generate",
            headers={"Authorization": "Bearer idem-pending-key", "Idempotency-Key": "pending-request"},
            json=request,
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "IDEMPOTENCY_KEY_IN_PROGRESS")
        conn = server.db_conn()
        task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        conn.close()
        self.assertEqual(task_count, 0)

    def test_concurrent_idempotent_http_requests_submit_queue_once(self):
        self.seed_account_with_capabilities()
        api_key_id = self.seed_api_key("idem-route-concurrency-key")
        request = self.valid_image_request()
        headers = {
            "Authorization": "Bearer idem-route-concurrency-key",
            "Idempotency-Key": "shared-route-request",
        }
        original_queue_generation_task = server.queue_generation_task
        queue_entered = threading.Event()
        release_first_queue = threading.Event()
        response_completed = threading.Event()
        result_lock = threading.Lock()
        responses = []
        errors = []
        queue_call_count = 0

        def delayed_queue_generation_task(*args, **kwargs):
            nonlocal queue_call_count
            with result_lock:
                queue_call_count += 1
                call_number = queue_call_count
            if call_number == 1:
                queue_entered.set()
                if not release_first_queue.wait(timeout=5):
                    raise TimeoutError("test queue gate was not released")
            return original_queue_generation_task(*args, **kwargs)

        def post_generation():
            client = TestClient(server.app)
            try:
                response = client.post(
                    "/v1/generate",
                    headers=headers,
                    json=request,
                )
                with result_lock:
                    responses.append(response)
                response_completed.set()
            except Exception as exc:
                with result_lock:
                    errors.append(exc)
                response_completed.set()
            finally:
                client.close()

        threads = [threading.Thread(target=post_generation)]
        duplicate_finished_while_first_was_queued = False
        with patch.object(server, "queue_generation_task", side_effect=delayed_queue_generation_task):
            threads[0].start()
            try:
                self.assertTrue(queue_entered.wait(timeout=5))
                duplicate_thread = threading.Thread(target=post_generation)
                threads.append(duplicate_thread)
                duplicate_thread.start()
                duplicate_finished_while_first_was_queued = response_completed.wait(timeout=5)
            finally:
                release_first_queue.set()
                for thread in threads:
                    thread.join(timeout=5)

        self.assertTrue(duplicate_finished_while_first_was_queued)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(queue_call_count, 1)
        self.assertEqual(sorted(response.status_code for response in responses), [202, 409])
        pending_response = next(response for response in responses if response.status_code == 409)
        accepted_response = next(response for response in responses if response.status_code == 202)
        self.assertEqual(pending_response.json()["error"]["code"], "IDEMPOTENCY_KEY_IN_PROGRESS")

        conn = server.db_conn()
        task_rows = conn.execute("SELECT id FROM tasks ORDER BY id").fetchall()
        idempotency_row = conn.execute(
            "SELECT task_id,status_code FROM idempotency_keys WHERE api_key_id=? AND idempotency_key=?",
            (api_key_id, "shared-route-request"),
        ).fetchone()
        conn.close()

        self.assertEqual(len(task_rows), 1)
        self.assertEqual(accepted_response.json()["task_id"], task_rows[0]["id"])
        self.assertEqual(idempotency_row["task_id"], task_rows[0]["id"])
        self.assertEqual(idempotency_row["status_code"], 202)

    def test_queue_failure_releases_idempotency_reservation(self):
        self.seed_account_with_capabilities()
        api_key_id = self.seed_api_key("idem-release-key")
        request = self.valid_image_request()

        with patch.object(server, "queue_generation_task", side_effect=RuntimeError("queue unavailable")):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    "/v1/generate",
                    headers={"Authorization": "Bearer idem-release-key", "Idempotency-Key": "release-request"},
                    json=request,
                )

        conn = server.db_conn()
        row = conn.execute(
            "SELECT * FROM idempotency_keys WHERE api_key_id=? AND idempotency_key=?",
            (api_key_id, "release-request"),
        ).fetchone()
        conn.close()
        self.assertIsNone(row)

    def test_generation_rejects_prompt_over_configured_limit(self):
        self.seed_api_key("prompt-limit-key")
        original = server.CFG["gateway"].get("prompt_max_length")
        server.CFG["gateway"]["prompt_max_length"] = 8
        try:
            response = self.client.post(
                "/v1/generate",
                headers={"Authorization": "Bearer prompt-limit-key"},
                json={**self.valid_image_request(), "prompt": "123456789"},
            )
        finally:
            server.CFG["gateway"]["prompt_max_length"] = original

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "PROMPT_TOO_LONG")

    def test_generation_rejects_oversized_idempotency_key(self):
        self.seed_api_key("idem-limit-key")
        original = server.CFG["gateway"].get("idempotency_key_max_length")
        server.CFG["gateway"]["idempotency_key_max_length"] = 8
        try:
            response = self.client.post(
                "/v1/generate",
                headers={"Authorization": "Bearer idem-limit-key", "Idempotency-Key": "123456789"},
                json=self.valid_image_request(),
            )
        finally:
            if original is None:
                server.CFG["gateway"].pop("idempotency_key_max_length", None)
            else:
                server.CFG["gateway"]["idempotency_key_max_length"] = original

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "IDEMPOTENCY_KEY_TOO_LONG")

    def test_generation_rejects_sync_wait_over_configured_limit(self):
        self.seed_api_key("sync-limit-key")
        original = server.CFG["gateway"].get("sync_wait_max_seconds")
        server.CFG["gateway"]["sync_wait_max_seconds"] = 2
        try:
            response = self.client.post(
                "/v1/generate",
                headers={"Authorization": "Bearer sync-limit-key"},
                json=self.valid_image_request(sync_wait_seconds=3),
            )
        finally:
            if original is None:
                server.CFG["gateway"].pop("sync_wait_max_seconds", None)
            else:
                server.CFG["gateway"]["sync_wait_max_seconds"] = original

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "SYNC_WAIT_OUT_OF_RANGE")

    def test_oversized_request_id_is_replaced_with_bounded_server_id(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("request-id-limit-key")
        original = server.CFG["gateway"].get("request_id_max_length")
        server.CFG["gateway"]["request_id_max_length"] = 16
        try:
            response = self.client.post(
                "/v1/generate",
                headers={"Authorization": "Bearer request-id-limit-key", "X-Request-ID": "x" * 17},
                json=self.valid_image_request(),
            )
        finally:
            if original is None:
                server.CFG["gateway"].pop("request_id_max_length", None)
            else:
                server.CFG["gateway"]["request_id_max_length"] = original

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()["request_id"].startswith("req_"))
        self.assertLessEqual(len(response.json()["request_id"]), 16)

    def test_idempotency_reservation_is_atomic_under_concurrency(self):
        api_key_id = self.seed_api_key("atomic-idempotency-key")
        barrier = threading.Barrier(6)
        results = []
        errors = []
        result_lock = threading.Lock()

        def reserve():
            try:
                barrier.wait(timeout=2)
                result = server.reserve_idempotency_record(
                    api_key_id,
                    "shared-request",
                    "same-request-hash",
                )
                with result_lock:
                    results.append(result)
            except Exception as exc:
                with result_lock:
                    errors.append(exc)

        threads = [threading.Thread(target=reserve) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual(errors, [])
        self.assertEqual(sum(item["state"] == "reserved" for item in results), 1)
        self.assertEqual(sum(item["state"] == "pending" for item in results), 5)
        conn = server.db_conn()
        count = conn.execute(
            "SELECT COUNT(*) FROM idempotency_keys WHERE api_key_id=? AND idempotency_key=?",
            (api_key_id, "shared-request"),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_idempotency_reservation_reclaims_expired_record(self):
        api_key_id = self.seed_api_key("ttl-idempotency-key")
        now = time.time()
        conn = server.db_conn()
        conn.execute(
            """
            INSERT INTO idempotency_keys(
                api_key_id,idempotency_key,request_hash,status_code,response_json,task_id,created_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (api_key_id, "expired-request", "old-hash", 202, "{}", None, now - 7200),
        )
        conn.commit()
        conn.close()
        original = server.CFG["gateway"].get("idempotency_ttl_hours")
        server.CFG["gateway"]["idempotency_ttl_hours"] = 1
        try:
            result = server.reserve_idempotency_record(
                api_key_id,
                "expired-request",
                "new-hash",
                now=now,
            )
        finally:
            server.CFG["gateway"]["idempotency_ttl_hours"] = original

        self.assertEqual(result["state"], "reserved")
        conn = server.db_conn()
        row = conn.execute(
            "SELECT * FROM idempotency_keys WHERE api_key_id=? AND idempotency_key=?",
            (api_key_id, "expired-request"),
        ).fetchone()
        conn.close()
        self.assertEqual(row["request_hash"], "new-hash")
        self.assertEqual(row["status_code"], 0)

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

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["error"]["code"], "RATE_LIMITED")

    def test_api_key_explicit_zero_limits_override_nonzero_gateway_defaults(self):
        api_key_id = self.seed_api_key(
            "unlimited-key",
            rate_limit_per_minute=0,
            daily_request_limit=0,
            daily_point_limit=0,
        )
        with patch.dict(
            server.CFG["gateway"],
            {
                "default_rate_limit_per_minute": 7,
                "default_daily_request_limit": 8,
                "default_daily_point_limit": 9,
            },
        ):
            policy = server.resolve_api_key_policy(server.get_api_key_record(api_key_id))

        self.assertEqual(policy["rate_limit_per_minute"], 0)
        self.assertEqual(policy["daily_request_limit"], 0)
        self.assertEqual(policy["daily_point_limit"], 0)

    def test_check_rate_limit_is_atomic_under_concurrency(self):
        class CoordinatedBuckets(dict):
            def __init__(self):
                super().__init__()
                self.read_barrier = threading.Barrier(2)

            def get(self, key, default=None):
                bucket = super().get(key, default)
                try:
                    self.read_barrier.wait(timeout=0.5)
                except threading.BrokenBarrierError:
                    pass
                return list(bucket)

        buckets = CoordinatedBuckets()
        result_lock = threading.Lock()
        admitted = []
        errors = []
        now = time.time()
        check_start = threading.Barrier(2)

        def check(request_id):
            try:
                check_start.wait(timeout=5)
                server.check_rate_limit(
                    999,
                    {"rate_limit_per_minute": 1},
                    now,
                    request_id,
                )
                with result_lock:
                    admitted.append(request_id)
            except Exception as exc:
                with result_lock:
                    errors.append(exc)

        threads = [
            threading.Thread(target=check, args=("rate-request-1",)),
            threading.Thread(target=check, args=("rate-request-2",)),
        ]
        with patch.object(server, "RATE_BUCKETS", buckets):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(admitted), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], server.GatewayAPIError)
        self.assertEqual(errors[0].code, "RATE_LIMITED")

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

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["error"]["code"], "DAILY_REQUEST_LIMIT_EXCEEDED")

    def test_concurrent_daily_request_limit_admission_allows_only_one_request(self):
        self.seed_account_with_capabilities()
        api_key_id = self.seed_api_key("concurrent-daily-key", daily_request_limit=1)
        original_check_daily_quota = server.check_daily_quota
        quota_checked = threading.Barrier(2)

        def synchronized_check_daily_quota(*args, **kwargs):
            original_check_daily_quota(*args, **kwargs)
            try:
                quota_checked.wait(timeout=0.5)
            except threading.BrokenBarrierError:
                pass

        requests = [self.valid_image_request(), self.valid_image_request()]
        requests[0]["prompt"] = "first concurrent request"
        requests[1]["prompt"] = "second concurrent request"
        with patch.object(server, "check_daily_quota", side_effect=synchronized_check_daily_quota):
            responses = self.post_generation_requests_concurrently("concurrent-daily-key", requests)

        self.assertEqual(sorted(response.status_code for response in responses), [202, 429])
        rejected = next(response for response in responses if response.status_code == 429)
        self.assertEqual(rejected.json()["error"]["code"], "DAILY_REQUEST_LIMIT_EXCEEDED")

        conn = server.db_conn()
        task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        usage_count = conn.execute(
            "SELECT COUNT(*) FROM usage_log WHERE api_key_id=?",
            (api_key_id,),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(task_count, 1)
        self.assertEqual(usage_count, 1)

    def test_concurrent_daily_point_limit_admission_allows_only_one_request(self):
        self.seed_account_with_capabilities()
        api_key_id = self.seed_api_key("concurrent-point-key", daily_point_limit=20)
        original_check_daily_quota = server.check_daily_quota
        quota_checked = threading.Barrier(2)

        def synchronized_check_daily_quota(*args, **kwargs):
            original_check_daily_quota(*args, **kwargs)
            try:
                quota_checked.wait(timeout=0.5)
            except threading.BrokenBarrierError:
                pass

        requests = [self.valid_image_request(), self.valid_image_request()]
        requests[0]["prompt"] = "first concurrent point request"
        requests[1]["prompt"] = "second concurrent point request"
        with patch.object(server, "check_daily_quota", side_effect=synchronized_check_daily_quota):
            responses = self.post_generation_requests_concurrently("concurrent-point-key", requests)

        self.assertEqual(sorted(response.status_code for response in responses), [202, 429])
        rejected = next(response for response in responses if response.status_code == 429)
        self.assertEqual(rejected.json()["error"]["code"], "DAILY_POINT_LIMIT_EXCEEDED")

        conn = server.db_conn()
        task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        usage_count = conn.execute(
            "SELECT COUNT(*) FROM usage_log WHERE api_key_id=?",
            (api_key_id,),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(task_count, 1)
        self.assertEqual(usage_count, 1)

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

    def test_scheduler_prefers_account_with_fewer_inflight_tasks(self):
        first_account_id = self.seed_account_with_capabilities("least-busy-1@example.com")
        second_account_id = self.seed_account_with_capabilities("least-busy-2@example.com")
        self.seed_api_key("least-busy-key")

        first_request = self.valid_image_request()
        first_request["prompt"] = "first queued request"
        second_request = self.valid_image_request()
        second_request["prompt"] = "second queued request"

        first = self.client.post(
            "/v1/generate",
            headers={"Authorization": "Bearer least-busy-key"},
            json=first_request,
        )
        second = self.client.post(
            "/v1/generate",
            headers={"Authorization": "Bearer least-busy-key"},
            json=second_request,
        )

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        selected_account_ids = [first.json()["account_id"], second.json()["account_id"]]
        self.assertEqual(set(selected_account_ids), {first_account_id, second_account_id})
        self.assertNotEqual(selected_account_ids[0], selected_account_ids[1])

    def test_scheduler_prefers_previously_successful_account_over_untested_account(self):
        proven_account_id = self.seed_account_with_capabilities("proven@example.com")
        untested_account_id = self.seed_account_with_capabilities("untested@example.com")
        now = time.time()
        conn = server.db_conn()
        conn.execute(
            "UPDATE accounts SET last_used_at=?, updated_at=? WHERE id=?",
            (now - 60, now - 60, proven_account_id),
        )
        conn.execute(
            "UPDATE accounts SET last_used_at=NULL, updated_at=? WHERE id=?",
            (now, untested_account_id),
        )
        conn.commit()
        conn.close()

        candidates = server.candidate_accounts_for_generation("image")

        self.assertEqual(candidates[0]["id"], proven_account_id)
        self.assertEqual({row["id"] for row in candidates}, {proven_account_id, untested_account_id})

    def test_scheduler_skips_verified_account_without_session_credentials(self):
        now = time.time()
        conn = server.db_conn()
        conn.execute(
            """
            INSERT INTO accounts(
                email,password,status,source,ouid,ouss,
                model_info_json,video_info_json,created_at,updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "missing-session@example.com",
                "",
                "verified",
                "manual",
                "",
                "",
                json.dumps(self.sample_image_info()),
                json.dumps(self.sample_video_info()),
                now,
                now,
            ),
        )
        conn.commit()
        conn.close()

        candidates = server.candidate_accounts_for_generation("image")

        self.assertEqual(candidates, [])

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
            patch.object(server.CLIENT, "hydrate_generation_result", return_value={"raw": {}, "assets": ["https://cdn.oreateai.com/static/result/ready.jpg"]}),
        ):
            response = self.client.post("/v1/generate", headers={"Authorization": "Bearer cooldown-key"}, json=self.valid_image_request(sync_wait_seconds=1))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["account_id"], ready_id)

    def test_scheduler_skips_low_balance_account_for_expensive_request(self):
        low_id = self.seed_account_with_capabilities("low-balance@example.com")
        ready_id = self.seed_account_with_capabilities("ready-balance@example.com")
        self.seed_api_key("balance-key")
        now = time.time()
        conn = server.db_conn()
        conn.execute(
            "UPDATE accounts SET rest_point=?, daily_point=?, bonus_point=?, balance_updated_at=?, updated_at=? WHERE id=?",
            (2, 0, 2, now, now + 100, low_id),
        )
        conn.execute(
            "UPDATE accounts SET rest_point=?, daily_point=?, bonus_point=?, balance_updated_at=?, updated_at=? WHERE id=?",
            (127, 27, 100, now, now, ready_id),
        )
        conn.commit()
        conn.close()

        selected_accounts = []

        def session_from_account(account):
            selected_accounts.append(account["id"])
            return object()

        with (
            patch.object(server.CLIENT, "session_from_account", side_effect=session_from_account),
            patch.object(server.CLIENT, "create_chat_session", return_value={"chatId": "chat-ready"}),
            patch.object(server.CLIENT, "stream_generation", return_value={"events": [{"event": "end"}], "error": None}),
            patch.object(server.CLIENT, "hydrate_generation_result", return_value={"raw": {}, "assets": ["https://cdn.oreateai.com/static/result/ready.jpg"]}),
        ):
            response = self.client.post("/v1/generate", headers={"Authorization": "Bearer balance-key"}, json=self.valid_image_request(sync_wait_seconds=1))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["account_id"], ready_id)
        self.assertTrue(selected_accounts)
        self.assertTrue(all(account_id == ready_id for account_id in selected_accounts))

    def test_admin_can_refresh_account_balance_and_list_safe_snapshot(self):
        account_id = self.seed_account_with_capabilities(email=f"balance-{time.time_ns()}@example.com")

        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()) as session_mock,
            patch.object(
                server.CLIENT,
                "fetch_account_point_detail",
                return_value={"data": {"daily": 27, "bonus": 100, "restPoint": 127}},
            ) as fetch_mock,
        ):
            response = self.client.post(f"/api/accounts/{account_id}/refresh-balance", headers=self.admin_headers())

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        item = payload["item"]
        self.assertEqual(item["rest_point"], 127)
        self.assertEqual(item["daily_point"], 27)
        self.assertEqual(item["bonus_point"], 100)
        self.assertIn("balance_updated_at", item)
        self.assertNotIn("point_balance_json", item)
        session_mock.assert_called_once()
        fetch_mock.assert_called_once()

        conn = server.db_conn()
        row = conn.execute(
            "SELECT rest_point,daily_point,bonus_point,balance_updated_at,point_balance_json FROM accounts WHERE id=?",
            (account_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(row["rest_point"], 127)
        self.assertEqual(row["daily_point"], 27)
        self.assertEqual(row["bonus_point"], 100)
        self.assertIsNotNone(row["balance_updated_at"])
        self.assertEqual(json.loads(row["point_balance_json"])["rest_point"], 127)

        accounts = self.client.get("/api/accounts", headers=self.admin_headers()).json()["items"]
        self.assertEqual(accounts[0]["rest_point"], 127)
        self.assertNotIn("point_balance_json", accounts[0])

    def test_normalize_account_point_detail_supports_current_amount_objects(self):
        snapshot = server.normalize_account_point_detail(
            {
                "daily": {"amount": 46, "endTime": 1783915200},
                "pro": {"amount": 12, "endTime": 1783915200},
                "bonus": {"amount": 100, "endTime": 2414290578},
            }
        )

        self.assertEqual(snapshot["daily_point"], 46)
        self.assertEqual(snapshot["bonus_point"], 100)
        self.assertEqual(snapshot["rest_point"], 158)
        self.assertEqual(snapshot["point_balance_json"]["pro_point"], 12)

    def test_upstream_failure_marks_account_cooldown(self):
        account_id = self.seed_account_with_capabilities()
        self.seed_api_key("fail-key")
        client = TestClient(server.app, raise_server_exceptions=False)

        with (
            patch.object(server.CLIENT, "session_from_account", return_value=object()),
            patch.object(server.CLIENT, "create_chat_session", side_effect=RuntimeError("upstream down")),
        ):
            response = client.post("/v1/generate", headers={"Authorization": "Bearer fail-key"}, json=self.valid_image_request(sync_wait_seconds=1))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "UPSTREAM_ERROR")
        conn = server.db_conn()
        row = conn.execute("SELECT failure_count,cooldown_until,last_error FROM accounts WHERE id=?", (account_id,)).fetchone()
        conn.close()
        self.assertEqual(row["failure_count"], 1)
        self.assertGreater(row["cooldown_until"], time.time())
        self.assertIn("upstream down", row["last_error"])

    def test_gateway_risk_failure_does_not_rotate_or_penalize_accounts(self):
        first_account_id = self.seed_account_with_capabilities("failover-first@example.com")
        second_account_id = self.seed_account_with_capabilities("failover-second@example.com")
        self.seed_api_key("automatic-failover-key")
        attempted_account_ids = []

        def submit(account, *_args, **_kwargs):
            attempted_account_ids.append(account["id"])
            raise server.UpstreamGenerationError({"code": "212361", "message": "spam user"})

        with (
            patch.object(server, "capture_account_balance_snapshot", return_value=None),
            patch.object(server, "submit_generation_for_account", side_effect=submit),
        ):
            response = self.client.post(
                "/v1/generate",
                headers={"Authorization": "Bearer automatic-failover-key"},
                json=self.valid_image_request(sync_wait_seconds=1),
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "212361")
        task_id = response.json()["error"]["details"]["task_id"]
        task = self.client.get(
            f"/v1/tasks/{task_id}",
            headers={"Authorization": "Bearer automatic-failover-key"},
        ).json()["task"]
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["attempt_count"], 1)
        self.assertEqual(len(task["attempts"]), 1)
        self.assertEqual(task["attempts"][0]["error_code"], "212361")
        self.assertEqual(len(attempted_account_ids), 1)
        self.assertIn(attempted_account_ids[0], {first_account_id, second_account_id})

        conn = server.db_conn()
        failed_account = conn.execute(
            "SELECT failure_count,cooldown_until,last_error FROM accounts WHERE id=?",
            (attempted_account_ids[0],),
        ).fetchone()
        usage_rows = conn.execute(
            "SELECT account_id,status,error_code FROM usage_log WHERE task_id=?",
            (task["id"],),
        ).fetchall()
        conn.close()
        self.assertEqual(failed_account["failure_count"], 0)
        self.assertIsNone(failed_account["cooldown_until"])
        self.assertIn("212361", failed_account["last_error"])
        self.assertEqual(len(usage_rows), 1)
        self.assertEqual(usage_rows[0]["account_id"], attempted_account_ids[0])
        self.assertEqual(usage_rows[0]["status"], "failed")
        self.assertEqual(usage_rows[0]["error_code"], "212361")

    def test_parameter_error_does_not_rotate_through_account_pool(self):
        self.seed_account_with_capabilities("params-first@example.com")
        self.seed_account_with_capabilities("params-second@example.com")
        self.seed_api_key("non-retryable-params-key")
        attempted_account_ids = []

        def submit(account, *_args, **_kwargs):
            attempted_account_ids.append(account["id"])
            raise server.UpstreamGenerationError({"code": "200002", "message": "params error"})

        with (
            patch.object(server, "capture_account_balance_snapshot", return_value=None),
            patch.object(server, "submit_generation_for_account", side_effect=submit),
        ):
            response = self.client.post(
                "/v1/generate",
                headers={"Authorization": "Bearer non-retryable-params-key"},
                json=self.valid_image_request(sync_wait_seconds=1),
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "200002")
        self.assertEqual(len(attempted_account_ids), 1)
        task = response.json()["error"]["details"]["task_id"]
        detail = self.client.get(
            f"/v1/tasks/{task}",
            headers={"Authorization": "Bearer non-retryable-params-key"},
        ).json()["task"]
        self.assertEqual(detail["attempt_count"], 1)
        self.assertEqual(len(detail["attempts"]), 1)

    def test_account_failover_still_applies_to_expired_sessions(self):
        for index in range(3):
            self.seed_account_with_capabilities(f"bounded-failover-{index}@example.com")
        self.seed_api_key("bounded-failover-key")
        attempted_account_ids = []
        original_cfg = server.CFG
        server.CFG = server.deep_merge(
            original_cfg,
            {"gateway": {"account_failover_max_attempts": 2}},
        )

        def submit(account, *_args, **_kwargs):
            attempted_account_ids.append(account["id"])
            raise server.UpstreamGenerationError({"code": "200001", "message": "session expired"})

        try:
            with (
                patch.object(server, "capture_account_balance_snapshot", return_value=None),
                patch.object(server, "submit_generation_for_account", side_effect=submit),
            ):
                response = self.client.post(
                    "/v1/generate",
                    headers={"Authorization": "Bearer bounded-failover-key"},
                    json=self.valid_image_request(sync_wait_seconds=1),
                )
        finally:
            server.CFG = original_cfg

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "200001")
        self.assertEqual(len(attempted_account_ids), 2)
        self.assertEqual(len(set(attempted_account_ids)), 2)
        task_id = response.json()["error"]["details"]["task_id"]
        detail = self.client.get(
            f"/v1/tasks/{task_id}",
            headers={"Authorization": "Bearer bounded-failover-key"},
        ).json()["task"]
        self.assertEqual(detail["attempt_count"], 2)
        self.assertEqual([attempt["status"] for attempt in detail["attempts"]], ["failed", "failed"])

    def test_explicit_account_request_does_not_switch_to_another_account(self):
        requested_account_id = self.seed_account_with_capabilities("fixed-account@example.com")
        self.seed_account_with_capabilities("fixed-account-spare@example.com")
        self.seed_api_key("fixed-account-key")
        attempted_account_ids = []
        request = self.valid_image_request(sync_wait_seconds=1)
        request["account_id"] = requested_account_id

        def submit(account, *_args, **_kwargs):
            attempted_account_ids.append(account["id"])
            raise server.UpstreamGenerationError({"code": "212361", "message": "spam user"})

        with (
            patch.object(server, "capture_account_balance_snapshot", return_value=None),
            patch.object(server, "submit_generation_for_account", side_effect=submit),
        ):
            response = self.client.post(
                "/v1/generate",
                headers={"Authorization": "Bearer fixed-account-key"},
                json=request,
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(attempted_account_ids, [requested_account_id])

    def test_session_expired_upstream_error_marks_account_invalid(self):
        account_id = self.seed_account_with_capabilities()

        server.mark_account_failure(
            account_id,
            server.UpstreamGenerationError({"code": "200001", "message": "session expired"}),
        )

        conn = server.db_conn()
        row = conn.execute("SELECT status,failure_count,cooldown_until,last_error FROM accounts WHERE id=?", (account_id,)).fetchone()
        conn.close()
        self.assertEqual(row["status"], "invalid")
        self.assertEqual(row["failure_count"], 1)
        self.assertIsNone(row["cooldown_until"])
        self.assertIn("200001", row["last_error"])

    def test_params_error_cools_account_without_invalidating_it(self):
        account_id = self.seed_account_with_capabilities()

        server.mark_account_failure(
            account_id,
            server.UpstreamGenerationError({"code": "200002", "message": "params error"}),
        )

        conn = server.db_conn()
        row = conn.execute("SELECT status,failure_count,cooldown_until,last_error FROM accounts WHERE id=?", (account_id,)).fetchone()
        conn.close()
        self.assertEqual(row["status"], "verified")
        self.assertEqual(row["failure_count"], 1)
        self.assertGreater(row["cooldown_until"], time.time())
        self.assertIn("200002", row["last_error"])

    def test_gateway_risk_error_records_diagnostic_without_penalizing_account(self):
        account_id = self.seed_account_with_capabilities()

        server.mark_account_failure(
            account_id,
            server.UpstreamGenerationError({"code": "212361", "message": "risk control"}),
        )

        conn = server.db_conn()
        row = conn.execute("SELECT status,failure_count,cooldown_until,last_error FROM accounts WHERE id=?", (account_id,)).fetchone()
        conn.close()
        self.assertEqual(row["status"], "verified")
        self.assertEqual(row["failure_count"], 0)
        self.assertIsNone(row["cooldown_until"])
        self.assertIn("212361", row["last_error"])

    def test_browser_generation_worker_receives_secrets_only_via_stdin(self):
        account_id = self.seed_account_with_capabilities("browser-worker@example.com")
        account = server.account_row_by_id(account_id)
        options = {
            "model_name": "Google Nano Banana 2",
            "ratio": "1:1",
            "resolution": "2K",
        }
        completed = subprocess.CompletedProcess(
            args=["node"],
            returncode=0,
            stdout=json.dumps(
                {
                    "chat": {"chatId": "browser-chat", "focusId": "browser-focus"},
                    "stream": {
                        "events": [{"event": "end"}],
                        "error": None,
                        "status": "streamed",
                        "completion_reason": "end",
                    },
                }
            ),
            stderr="",
        )
        original_cfg = server.CFG
        server.CFG = server.deep_merge(
            original_cfg,
            {
                "oreate": {
                    "browser_worker_enabled": True,
                    "browser_worker_node": "node",
                    "browser_worker_timeout_seconds": 150,
                    "chromium_executable": "/usr/bin/chromium-browser",
                    "browser_worker_node_modules": "/var/lib/oreateai/browser-worker/node_modules",
                }
            },
        )
        try:
            with patch.object(server.subprocess, "run", return_value=completed) as run:
                result = server.run_browser_generation(
                    account,
                    "image",
                    "生成一只小猫",
                    options,
                    image_config=server.build_image_config(options),
                    video_config=None,
                    attachments=[],
                )
        finally:
            server.CFG = original_cfg

        self.assertEqual(result["chat"]["chatId"], "browser-chat")
        command = run.call_args.args[0]
        self.assertNotIn("browser-worker@example.com", " ".join(command))
        self.assertNotIn("plain-password", " ".join(command))
        payload = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(payload["account"]["email"], "browser-worker@example.com")
        self.assertTrue(payload["account"]["ouid"])
        self.assertTrue(payload["account"]["ouss"])
        self.assertEqual(payload["runtime"]["streamWaitMs"], 120_000)
        self.assertEqual(run.call_args.kwargs["timeout"], 150)
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_browser_generation_worker_allows_slow_video_page_readiness(self):
        account_id = self.seed_account_with_capabilities("slow-video-page@example.com")
        account = server.account_row_by_id(account_id)
        options = {
            "model_name": "Seedance 2.0 Mini",
            "ratio": "16:9",
            "resolution": "480",
            "duration": 5,
            "scene_id": "text_or_image",
        }
        completed = subprocess.CompletedProcess(
            args=["node"],
            returncode=0,
            stdout=json.dumps(
                {
                    "chat": {"chatId": "video-chat", "focusId": "video-focus"},
                    "stream": {
                        "events": [{"event": "end"}],
                        "error": None,
                        "status": "streamed",
                        "completion_reason": "end",
                    },
                }
            ),
            stderr="",
        )
        original_cfg = server.CFG
        server.CFG = server.deep_merge(
            original_cfg,
            {
                "oreate": {
                    "browser_worker_enabled": True,
                    "browser_worker_node": "node",
                    "browser_worker_timeout_seconds": 180,
                    "browser_worker_readiness_timeout_seconds": 60,
                    "chromium_executable": "/usr/bin/chromium-browser",
                    "browser_worker_node_modules": "/var/lib/oreateai/browser-worker/node_modules",
                }
            },
        )
        try:
            with patch.object(server.subprocess, "run", return_value=completed) as run:
                server.run_browser_generation(
                    account,
                    "video",
                    "生成一只小猫行走的视频",
                    options,
                    image_config=None,
                    video_config=server.build_video_config(options),
                    attachments=[],
                )
        finally:
            server.CFG = original_cfg

        payload = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(payload["runtime"].get("readinessTimeoutMs"), 60_000)
        worker_source = server.browser_worker_script_path().read_text(encoding="utf-8")
        self.assertIn("runtime.readinessTimeoutMs", worker_source)
        self.assertNotIn("{timeout: 30000}", worker_source)

    def test_submit_generation_uses_browser_stream_assets_when_hydration_lags(self):
        account_id = self.seed_account_with_capabilities("browser-submit@example.com")
        account = server.account_row_by_id(account_id)
        options = {
            "model_name": "Google Nano Banana 2",
            "ratio": "1:1",
            "resolution": "2K",
        }
        asset_url = "https://cdn.oreateai.com/gpt-image/result.png"
        original_cfg = server.CFG
        server.CFG = server.deep_merge(
            original_cfg,
            {"oreate": {"browser_worker_enabled": True}},
        )
        try:
            with (
                patch.object(server.CLIENT, "session_from_account", return_value=object()),
                patch.object(
                    server,
                    "run_browser_generation",
                    return_value={
                        "chat": {"chatId": "browser-chat", "focusId": "browser-focus"},
                        "stream": {
                            "events": [
                                {
                                    "event": "generating",
                                    "data": {"url": asset_url},
                                },
                                {"event": "end"},
                            ],
                            "error": None,
                            "status": "streamed",
                        },
                    },
                ) as run_browser,
                patch.object(
                    server.CLIENT,
                    "hydrate_generation_result",
                    return_value={"raw": {}, "assets": []},
                ),
                patch.object(server.CLIENT, "create_chat_session") as create_chat,
                patch.object(server.CLIENT, "stream_generation") as stream_generation,
            ):
                result = server.submit_generation_for_account(
                    account,
                    "image",
                    "生成一只小猫",
                    options,
                )
        finally:
            server.CFG = original_cfg

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["assets"], [asset_url])
        run_browser.assert_called_once()
        create_chat.assert_not_called()
        stream_generation.assert_not_called()

    def test_hydration_no_message_error_does_not_penalize_account(self):
        account_id = self.seed_account_with_capabilities()

        server.mark_account_failure(
            account_id,
            server.UpstreamGenerationError({"code": "110012", "message": "message not found"}),
        )

        conn = server.db_conn()
        row = conn.execute("SELECT status,failure_count,cooldown_until,last_error FROM accounts WHERE id=?", (account_id,)).fetchone()
        conn.close()
        self.assertEqual(row["status"], "verified")
        self.assertEqual(row["failure_count"], 0)
        self.assertIsNone(row["cooldown_until"])
        self.assertIn("110012", row["last_error"])

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
            patch.object(server.CLIENT, "hydrate_generation_result", return_value={"raw": {}, "assets": ["https://cdn.oreateai.com/static/result/success.jpg"]}),
        ):
            response = self.client.post("/v1/generate", headers={"Authorization": "Bearer success-key"}, json=self.valid_image_request(sync_wait_seconds=1))

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

        self.assertEqual(created.status_code, 202)
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
        self.assertIn("allowed_kinds", html)
        self.assertIn("allowed_models", html)
        self.assertIn("allowed_scenes", html)
        self.assertIn("allowed_resolutions", html)
        self.assertIn("allowed_durations", html)
        self.assertIn("allow_uploads", html)
        self.assertIn("allow_experimental", html)
        self.assertIn("updateApiKeyPolicy", html)
        self.assertIn("estimated_point_cost", html)
        self.assertIn("actual_point_cost", html)
        self.assertIn("error_code", html)
        self.assertIn("client_id", html)
        self.assertNotIn('onclick="createClient()"', html)
        self.assertIn("loadCostReport", html)
        self.assertIn("/api/admin/cost-report", html)
        self.assertIn("loadAuditLogs", html)
        self.assertIn("audit-tbody", html)
        self.assertIn("/api/admin/logout", html)
        self.assertIn("/api/admin/audit-logs", html)
        self.assertIn("downloadBackup", html)
        self.assertIn("restoreBackup", html)
        self.assertIn("/api/admin/backup", html)
        self.assertIn("/api/admin/restore", html)

    def test_admin_html_treats_api_key_as_customer_and_uses_an_editor_drawer(self):
        html = server.ADMIN_HTML

        self.assertIn('id="apikeys-key-panel"', html)
        self.assertIn('id="apikey-editor-backdrop"', html)
        self.assertIn('id="apikey-editor"', html)
        self.assertIn("openApiKeyEditor", html)
        self.assertIn("closeApiKeyEditor", html)
        self.assertIn("copyApiKey", html)
        self.assertIn("客户名称", html)
        self.assertIn("每日点数额度", html)
        self.assertNotIn('id="clients-tbody"', html)
        self.assertNotIn('id="client-name"', html)
        self.assertNotIn('onclick="createClient()"', html)

        script = html.split("<script>", 1)[1].split("</script>", 1)[0]
        render_start = script.index("function renderApiKeys(")
        render_end = script.index("function renderClients(", render_start)
        render_source = script[render_start:render_end]
        self.assertNotIn('id="ak-rate-${k.id}"', render_source)
        self.assertNotIn('id="ak-point-${k.id}"', render_source)
        self.assertIn("今日用量", html)
        self.assertIn("编辑", render_source)

    def test_admin_html_preserves_api_key_limit_semantics(self):
        html = server.ADMIN_HTML
        self.assertIn("function optionalNonNegativeIntegerValue", html)
        self.assertIn("String(rawValue ?? '')", html)
        self.assertNotIn("k.rate_limit_per_minute||''", html)
        self.assertIn("rate_limit_per_minute:optionalNonNegativeIntegerValue(", html)
        self.assertIn("daily_request_limit:optionalNonNegativeIntegerValue(", html)
        self.assertIn("daily_point_limit:optionalNonNegativeIntegerValue(", html)
        self.assertIn("function apiKeyLimitInputValue", html)
        self.assertIn("apiKeyLimitInputValue(key?.rate_limit_per_minute)", html)
        self.assertIn("apiKeyLimitInputValue(key?.daily_request_limit)", html)
        self.assertIn("apiKeyLimitInputValue(key?.daily_point_limit)", html)
        self.assertIn("k.status", html)
        self.assertIn("k.status ?? (k.enabled ? 'enabled':'disabled')", html)
        self.assertIn("apiKeyStatusTagClass", html)
        self.assertIn("escapeHtml(adminLabel('apiKeyStatus',keyStatus))", html)
        self.assertIn("escapeHtml(k.key_preview||'')", html)
        self.assertIn('placeholder="留空继承"', html)
        self.assertIn("0 表示不限额", html)

        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required to execute the API key limit helper test")
        script = html.split("<script>", 1)[1].split("</script>", 1)[0]

        def source_between(start_marker, end_marker):
            start = script.index(start_marker)
            end = script.index(end_marker, start)
            return script[start:end].strip()

        scope_source = source_between("function scopeCsv(", "function optionalNonNegativeIntegerValue(")
        helper_source = source_between("function optionalNonNegativeIntegerValue(", "function apiKeyLimitInputValue(")
        input_value_source = source_between("function apiKeyLimitInputValue(", "function apiKeyStatusTagClass(")
        editor_body_source = source_between("function apiKeyEditorBody(", "async function createApiKey(")
        update_source = source_between("async function updateApiKeyPolicy(", "async function saveApiKeyEditor(")
        self.assertNotIn("await loadClients();", update_source)
        node_program = f"""
{helper_source}
{input_value_source}
const validCases = [
  ['', null],
  ['   ', null],
  ['0', 0],
  ['7', 7],
  ['0007', 7],
  [0, 0],
  [42, 42],
];
for (const [input, expected] of validCases) {{
  const actual = optionalNonNegativeIntegerValue(input, '限额');
  if (actual !== expected) {{
    throw new Error(`input ${{JSON.stringify(input)}}: expected ${{expected}}, got ${{actual}}`);
  }}
  if (actual !== null && typeof actual !== 'number') {{
    throw new Error(`input ${{JSON.stringify(input)}} returned non-number ${{typeof actual}}`);
  }}
}}
for (const input of ['-1', '1.5', 'abc', '1e3', '9007199254740992']) {{
  let rejected = false;
  try {{
    optionalNonNegativeIntegerValue(input, '限额');
  }} catch (error) {{
    rejected = true;
  }}
  if (!rejected) throw new Error(`invalid input ${{JSON.stringify(input)}} was accepted`);
}}
for (const [input, expected] of [
  [null, ''],
  ['', ''],
  ['0', '0'],
  [0, '0'],
  ['17', '17'],
  [17, '17'],
  ['-1', ''],
  ['1.5', ''],
  ['not-a-number', ''],
  ['9007199254740992', ''],
]) {{
  const actual = apiKeyLimitInputValue(input);
  if (actual !== expected) {{
    throw new Error(`render value ${{JSON.stringify(input)}}: expected ${{JSON.stringify(expected)}}, got ${{JSON.stringify(actual)}}`);
  }}
}}
{scope_source}
const fields = {{
  'ak-editor-name': {{value: '测试客户'}},
  'ak-editor-note': {{value: '标准套餐'}},
  'ak-editor-rate': {{value: ''}},
  'ak-editor-requests': {{value: '0'}},
  'ak-editor-points': {{value: '17'}},
  'ak-editor-kind-image': {{checked: true}},
  'ak-editor-kind-video': {{checked: false}},
  'ak-editor-models': {{value: 'model-a, model-a, model-b'}},
  'ak-editor-scenes': {{value: ''}},
  'ak-editor-resolutions': {{value: '1K,4K'}},
  'ak-editor-durations': {{value: '5,10'}},
  'ak-editor-uploads': {{checked: true}},
  'ak-editor-experimental': {{checked: false}},
  'ak-editor-enabled': {{checked: true}},
}};
const document = {{getElementById: id => fields[id]}};
{editor_body_source}
const editorBody = apiKeyEditorBody();
const expectedEditorLimits = {{rate_limit_per_minute: null, daily_request_limit: 0, daily_point_limit: 17}};
for (const [field, value] of Object.entries(expectedEditorLimits)) {{
  if (editorBody[field] !== value) throw new Error(`${{field}} editor value mismatch`);
}}
if (JSON.stringify(editorBody.allowed_models) !== JSON.stringify(['model-a','model-b'])) {{
  throw new Error(`model scope was not normalized: ${{JSON.stringify(editorBody.allowed_models)}}`);
}}
let capturedBody = undefined;
let patchCalls = 0;
async function api(method, path, body) {{
  if (method !== 'PATCH' || path !== '/api/admin/apikeys/9') throw new Error('unexpected API call');
  patchCalls += 1;
  capturedBody = body;
  return {{ok: true}};
}}
async function loadApiKeys() {{}}
const alerts = [];
function alert(message) {{ alerts.push(message); }}
{update_source}
(async () => {{
  await updateApiKeyPolicy(9, editorBody);
  if (alerts.length) throw new Error(`unexpected alert: ${{alerts.join(' | ')}}`);
  const expected = {{rate_limit_per_minute: null, daily_request_limit: 0, daily_point_limit: 17}};
  for (const [field, value] of Object.entries(expected)) {{
    if (capturedBody?.[field] !== value) {{
      throw new Error(`${{field}}: expected ${{value}}, got ${{capturedBody?.[field]}}`);
    }}
    if (value !== null && typeof capturedBody[field] !== 'number') {{
      throw new Error(`${{field}} was not sent as a number`);
    }}
  }}
  loadApiKeys = async () => {{ throw new Error('refresh boom'); }};
  await updateApiKeyPolicy(9, editorBody);
  if (patchCalls !== 2) throw new Error(`expected two successful PATCH calls, got ${{patchCalls}}`);
  if (alerts.length !== 1 || !alerts[0].includes('已保存但刷新失败')) {{
    throw new Error(`refresh failure alert was misleading: ${{alerts.join(' | ')}}`);
  }}
  if (alerts[0].includes('保存失败：')) {{
    throw new Error(`refresh failure was reported as save failure: ${{alerts[0]}}`);
  }}
}})().catch(error => {{
  console.error(error);
  process.exitCode = 1;
}});
"""
        node_test_path = Path(self.tmp.name) / "api_key_limit_helper_test.js"
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

    def test_admin_html_gates_task_actions_by_status(self):
        html = server.ADMIN_HTML
        self.assertIn("function taskCanRetry", html)
        self.assertIn("function taskCanHydrate", html)
        self.assertIn("function taskCanCancel", html)
        self.assertIn("function taskActionButtons", html)
        self.assertIn("taskActionButtons(t)", html)
        self.assertIn("taskActionButtons(task)", html)
        self.assertIn("确认取消任务 #${id}", html)
        self.assertNotIn('onclick="hydrateTask(${t.id})">重水合</button>', html)
        self.assertNotIn('onclick="retryTask(${t.id})">重试</button>', html)
        self.assertNotIn('onclick="cancelTask(${t.id})">取消</button>', html)

        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required to execute the task action helper test")
        script = html.split("<script>", 1)[1].split("</script>", 1)[0]

        def source_between(start_marker, end_marker):
            start = script.index(start_marker)
            end = script.index(end_marker, start)
            return script[start:end].strip()

        escape_source = source_between("function escapeHtml(", "function normalizedOptionValues(")
        predicates_source = source_between("function taskCanRetry(", "function renderTasks(")
        render_source = source_between("function renderTasks(", "function renderTaskAsset(")
        action_source = source_between("async function runTaskAction(", "// === API Keys ===")
        node_program = f"""
{escape_source}
{predicates_source}
const expectations = {{
  queued:    {{retry:false, hydrate:false, cancel:true}},
  running:   {{retry:false, hydrate:false, cancel:true}},
  submitted: {{retry:false, hydrate:true,  cancel:true}},
  hydrating: {{retry:false, hydrate:true,  cancel:true}},
  failed:    {{retry:true,  hydrate:false, cancel:false}},
  expired:   {{retry:true,  hydrate:false, cancel:false}},
  completed: {{retry:false, hydrate:false, cancel:false}},
  cancelled: {{retry:false, hydrate:false, cancel:false}},
}};
for (const [status, expected] of Object.entries(expectations)) {{
  const actual = {{
    retry: taskCanRetry(status),
    hydrate: taskCanHydrate(status),
    cancel: taskCanCancel(status),
  }};
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {{
    throw new Error(`${{status}} predicates: expected ${{JSON.stringify(expected)}}, got ${{JSON.stringify(actual)}}`);
  }}
  const actions = taskActionButtons({{id: 17, status}});
  for (const [action, allowed] of Object.entries(expected)) {{
    const handler = action === 'retry' ? 'retryTask(17)' : action === 'hydrate' ? 'hydrateTask(17)' : 'cancelTask(17)';
    if (actions.includes(handler) !== allowed) {{
      throw new Error(`${{status}} ${{action}} button mismatch: ${{actions}}`);
    }}
  }}
}}
const tbody = {{innerHTML: ''}};
const state = {{tasks: Object.keys(expectations).map((status, index) => ({{
  id: index + 1,
  kind: 'video',
  status,
  prompt: status,
  created_at: 1,
}}))}};
const document = {{getElementById: id => {{
  if (id !== 'tasks-tbody') throw new Error(`unexpected element ${{id}}`);
  return tbody;
}}}};
{render_source}
renderTasks();
for (const [index, [status, expected]] of Object.entries(Object.entries(expectations))) {{
  const id = Number(index) + 1;
  for (const [action, allowed] of Object.entries(expected)) {{
    const handler = action === 'retry' ? `retryTask(${{id}})` : action === 'hydrate' ? `hydrateTask(${{id}})` : `cancelTask(${{id}})`;
    const rowStart = tbody.innerHTML.indexOf(`<td>${{id}}</td>`);
    const rowEnd = tbody.innerHTML.indexOf('</tr>', rowStart);
    const row = tbody.innerHTML.slice(rowStart, rowEnd);
    if (row.includes(handler) !== allowed) {{
      throw new Error(`${{status}} rendered ${{action}} mismatch: ${{row}}`);
    }}
  }}
}}
let apiCalls = 0;
let refreshCalls = 0;
const alerts = [];
let confirmResult = false;
async function api(method, path) {{
  apiCalls += 1;
  if (method !== 'POST' || path !== '/api/tasks/42/cancel') throw new Error('unexpected API call');
  return {{task: {{id: 42, status: 'cancelled'}}}};
}}
async function loadTasks() {{
  refreshCalls += 1;
  throw new Error('refresh boom');
}}
function renderTaskPreview() {{}}
function confirm(message) {{
  if (message !== '确认取消任务 #42？') throw new Error(`unexpected confirmation: ${{message}}`);
  return confirmResult;
}}
function alert(message) {{ alerts.push(String(message)); }}
{action_source}
(async () => {{
  await cancelTask(42);
  if (apiCalls !== 0) throw new Error('cancel API was called after confirmation rejection');
  confirmResult = true;
  await cancelTask(42);
  if (apiCalls !== 1) throw new Error(`expected one cancel API call, got ${{apiCalls}}`);
  if (refreshCalls !== 1) throw new Error(`expected one refresh, got ${{refreshCalls}}`);
  if (alerts.length !== 1 || !alerts[0].includes('取消成功，但列表刷新失败')) {{
    throw new Error(`refresh failure message was not action-specific: ${{alerts.join(' | ')}}`);
  }}
  if (alerts[0].includes('取消失败')) {{
    throw new Error(`refresh failure was reported as action failure: ${{alerts[0]}}`);
  }}
  api = async () => {{
    throw new Error('TASK_NOT_CANCELLABLE: only active tasks can be cancelled');
  }};
  await cancelTask(42);
  if (alerts.length !== 2 || !alerts[1].includes('TASK_NOT_CANCELLABLE')) {{
    throw new Error(`Gateway envelope error was not readable: ${{alerts.join(' | ')}}`);
  }}
}})().catch(error => {{
  console.error(error);
  process.exitCode = 1;
}});
"""
        node_test_path = Path(self.tmp.name) / "task_action_helper_test.js"
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

    def test_admin_html_paginates_operational_lists(self):
        html = server.ADMIN_HTML
        for element_id in (
            "task-filter-status",
            "task-filter-kind",
            "task-filter-model-name",
            "task-filter-scene-id",
            "task-filter-client-id",
            "task-filter-api-key-id",
            "task-filter-account-id",
            "task-filter-error-code",
            "task-filter-date-from",
            "task-filter-date-to",
            "task-page-size",
            "tasks-prev",
            "tasks-next",
            "tasks-list-status",
            "usage-filter-kind",
            "usage-filter-status",
            "usage-filter-model-name",
            "usage-filter-api-key-id",
            "usage-filter-account-id",
            "usage-filter-error-code",
            "usage-filter-date-from",
            "usage-filter-date-to",
            "usage-page-size",
            "usage-prev",
            "usage-next",
            "usage-list-status",
            "upload-filter-kind",
            "upload-filter-status",
            "upload-filter-api-key-id",
            "upload-filter-account-id",
            "upload-filter-date-from",
            "upload-filter-date-to",
            "upload-page-size",
            "uploads-prev",
            "uploads-next",
            "uploads-list-status",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn("new URLSearchParams()", html)
        self.assertIn("response.has_more", html)
        self.assertIn("response.total", html)
        self.assertIn("state.lists.tasks.total", html)
        self.assertNotIn("state.tasks.slice(0,50)", html)
        self.assertNotIn("state.usage.slice(0,50)", html)
        self.assertNotIn("(state.uploads||[]).slice(0,50)", html)

        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required to execute pagination helpers")
        script = html.split("<script>", 1)[1].split("</script>", 1)[0]

        def source_between(start_marker, end_marker):
            start = script.index(start_marker)
            end = script.index(end_marker, start)
            return script[start:end].strip()

        helper_source = source_between("function createListPageState(", "function formatApiError(")
        loader_source = source_between("function listFiltersFromInputs(", "function escapeHtml(")
        node_program = f"""
{helper_source}
{loader_source}
function escapeHtml(value) {{ return String(value ?? ''); }}
const elements = new Map();
const document = {{getElementById(id) {{
  if (!elements.has(id)) {{
    elements.set(id, {{
      value:'',
      textContent:'',
      disabled:false,
      classList:{{toggle() {{}}}},
    }});
  }}
  return elements.get(id);
}}}};
var api;
const page = createListPageState(25);
page.offset = 50;
page.filters = {{status:'failed', account_id:'7', empty:'', ignored:null}};
const params = listQueryParams(page);
const actualQuery = params.toString();
for (const expected of ['limit=25', 'offset=50', 'status=failed', 'account_id=7']) {{
  if (!actualQuery.includes(expected)) throw new Error(`missing query part ${{expected}}: ${{actualQuery}}`);
}}
if (actualQuery.includes('empty=') || actualQuery.includes('ignored=')) {{
  throw new Error(`blank filters leaked into query: ${{actualQuery}}`);
}}
const items = applyListPage(page, {{
  items:[{{id:1}}],
  limit:25,
  offset:50,
  total:73,
  has_more:false,
}});
if (items.length !== 1 || page.total !== 73 || page.offset !== 50 || page.hasMore !== false) {{
  throw new Error(`response was not applied: ${{JSON.stringify(page)}}`);
}}
if (!listCanPrevious(page) || listCanNext(page)) throw new Error('pagination boundary mismatch');
if (!listPageSummary(page).includes('第 3 / 3 页') || !listPageSummary(page).includes('共 73 条')) {{
  throw new Error(`unexpected summary: ${{listPageSummary(page)}}`);
}}
page.offset = 0;
page.total = 0;
page.hasMore = false;
if (listCanPrevious(page) || listCanNext(page)) throw new Error('empty page controls should be disabled');
if (!listPageSummary(page).includes('共 0 条')) throw new Error(`empty summary mismatch: ${{listPageSummary(page)}}`);
(async () => {{
  const taskPage=state.lists.tasks;
  taskPage.limit=25;
  const pending=[];
  api=(method,path) => new Promise(resolve => pending.push({{resolve,path}}));
  const render=() => {{}};
  const staleRequest=loadOperationalList('tasks','/api/tasks',render);
  taskPage.filters={{status:'failed'}};
  taskPage.offset=0;
  const freshRequest=loadOperationalList('tasks','/api/tasks',render);
  pending[1].resolve({{items:[{{id:2}}],limit:25,offset:0,total:1,has_more:false}});
  await freshRequest;
  pending[0].resolve({{items:[{{id:1}}],limit:25,offset:0,total:99,has_more:true}});
  await staleRequest;
  if (state.tasks.length !== 1 || state.tasks[0].id !== 2 || taskPage.total !== 1) {{
    throw new Error(`stale response overwrote current page: ${{JSON.stringify({{items:state.tasks,page:taskPage}})}}`);
  }}

  taskPage.offset=50;
  taskPage.filters={{}};
  let correctionCalls=0;
  api=async () => {{
    correctionCalls += 1;
    if (correctionCalls===1) return {{items:[],limit:25,offset:50,total:30,has_more:false}};
    return {{items:[{{id:3}}],limit:25,offset:25,total:30,has_more:false}};
  }};
  await loadOperationalList('tasks','/api/tasks',render);
  if (correctionCalls !== 2 || taskPage.offset !== 25 || state.tasks[0].id !== 3) {{
    throw new Error(`out-of-range page was not corrected: ${{JSON.stringify({{correctionCalls,page:taskPage,items:state.tasks}})}}`);
  }}

  taskPage.offset=25;
  let emptyCalls=0;
  api=async () => {{
    emptyCalls += 1;
    return {{items:[],limit:25,offset:25,total:0,has_more:false}};
  }};
  await loadOperationalList('tasks','/api/tasks',render);
  if (emptyCalls !== 1 || taskPage.offset !== 0 || state.tasks.length !== 0) {{
    throw new Error(`empty out-of-range page was not normalized: ${{JSON.stringify({{emptyCalls,page:taskPage,items:state.tasks}})}}`);
  }}

  api=async () => {{ throw new Error('network boom'); }};
  let rejected=false;
  try {{ await loadOperationalList('tasks','/api/tasks',render); }} catch (_error) {{ rejected=true; }}
  if (!rejected || taskPage.loading || taskPage.error !== 'network boom') {{
    throw new Error(`error state mismatch: ${{JSON.stringify(taskPage)}}`);
  }}
}})().catch(error => {{
  console.error(error);
  process.exitCode=1;
}});
"""
        node_test_path = Path(self.tmp.name) / "operational_list_pagination_test.js"
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

    def test_admin_html_contains_balance_refresh_controls(self):
        html = server.ADMIN_HTML
        self.assertIn("refreshAccountBalance", html)
        self.assertIn("刷新余额", html)
        self.assertIn("health_status", html)

    def test_accounts_response_includes_health_summary_fields(self):
        healthy_id = self.seed_account_with_capabilities("healthy@example.com")
        cooling_id = self.seed_account_with_capabilities("cooling@example.com")
        low_balance_id = self.seed_account_with_capabilities("low@example.com")
        risk_id = self.seed_account_with_capabilities("risk@example.com")
        now = time.time()
        conn = server.db_conn()
        conn.execute("UPDATE accounts SET rest_point=?, daily_point=?, bonus_point=?, balance_updated_at=? WHERE id=?", (127, 27, 100, now, healthy_id))
        conn.execute("UPDATE accounts SET rest_point=?, daily_point=?, bonus_point=?, balance_updated_at=?, cooldown_until=?, failure_count=? WHERE id=?", (127, 27, 100, now, now + 300, 1, cooling_id))
        conn.execute("UPDATE accounts SET rest_point=?, daily_point=?, bonus_point=?, balance_updated_at=? WHERE id=?", (5, 0, 5, now, low_balance_id))
        conn.execute("UPDATE accounts SET status='invalid', failure_count=?, cooldown_until=NULL, last_error=? WHERE id=?", (1, "212361: risk control", risk_id))
        conn.commit()
        conn.close()

        response = self.client.get("/api/accounts", headers=self.admin_headers())
        self.assertEqual(response.status_code, 200)
        items = {item["email"]: item for item in response.json()["items"]}

        self.assertEqual(items["healthy@example.com"]["health_status"], "healthy")
        self.assertEqual(items["healthy@example.com"]["risk_status"], "clean")
        self.assertEqual(items["healthy@example.com"]["balance_status"], "ok")
        self.assertFalse(items["healthy@example.com"]["cooling"])

        self.assertEqual(items["cooling@example.com"]["health_status"], "cooling")
        self.assertTrue(items["cooling@example.com"]["cooling"])
        self.assertGreater(items["cooling@example.com"]["cooldown_remaining_seconds"], 0)

        self.assertEqual(items["low@example.com"]["health_status"], "low_balance")
        self.assertEqual(items["low@example.com"]["balance_status"], "low")

        self.assertEqual(items["risk@example.com"]["health_status"], "invalid")
        self.assertEqual(items["risk@example.com"]["risk_status"], "invalid")

    def test_gateway_account_status_reports_health_counts(self):
        self.seed_account_with_capabilities("healthy@example.com")
        cooling_id = self.seed_account_with_capabilities("cooling@example.com")
        low_id = self.seed_account_with_capabilities("low@example.com")
        risk_id = self.seed_account_with_capabilities("risk@example.com")
        self.seed_api_key("pool-status-key")
        now = time.time()
        conn = server.db_conn()
        conn.execute("UPDATE accounts SET cooldown_until=?, failure_count=? WHERE id=?", (now + 300, 1, cooling_id))
        conn.execute("UPDATE accounts SET rest_point=?, daily_point=?, bonus_point=?, balance_updated_at=? WHERE id=?", (5, 0, 5, now, low_id))
        conn.execute("UPDATE accounts SET status='invalid', failure_count=?, cooldown_until=NULL, last_error=? WHERE id=?", (1, "212361: risk control", risk_id))
        conn.commit()
        conn.close()

        response = self.client.get("/v1/accounts/status", headers={"Authorization": "Bearer pool-status-key"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total_accounts"], 4)
        self.assertEqual(payload["verified_accounts"], 3)
        self.assertEqual(payload["healthy_accounts"], 1)
        self.assertEqual(payload["cooling_accounts"], 1)
        self.assertEqual(payload["low_balance_accounts"], 1)
        self.assertEqual(payload["invalid_accounts"], 1)
        self.assertEqual(payload["risk_control_accounts"], 0)

    def test_healthz_readyz_and_metrics_report_operational_state(self):
        health = self.client.get("/healthz")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json()["ok"])

        ready_before = self.client.get("/readyz")
        self.assertEqual(ready_before.status_code, 503)

        healthy_id = self.seed_account_with_capabilities("healthy@example.com")
        now = time.time()
        conn = server.db_conn()
        conn.execute("UPDATE accounts SET rest_point=?, daily_point=?, bonus_point=?, balance_updated_at=? WHERE id=?", (127, 27, 100, now, healthy_id))
        conn.commit()
        conn.close()
        self.seed_api_key("metrics-key")

        ready_after = self.client.get("/readyz")
        self.assertEqual(ready_after.status_code, 200)
        self.assertTrue(ready_after.json()["ok"])
        self.assertGreaterEqual(ready_after.json()["healthy_accounts"], 1)

        now = time.time()
        server.save_task(
            healthy_id,
            "image",
            "queued prompt",
            self.valid_image_request(),
            {"status": "queued"},
            status="queued",
            request_id="req-queued",
        )
        server.save_task(
            healthy_id,
            "image",
            "failed prompt",
            self.valid_image_request(),
            {"status": "failed", "error": {"code": "UPSTREAM_ERROR"}},
            status="failed",
            error_code="UPSTREAM_ERROR",
            error_message="upstream error",
            request_id="req-failed",
            finished_at=now,
        )
        server.save_task(
            healthy_id,
            "video",
            "done prompt",
            self.valid_video_request(),
            {"status": "completed", "chat": {"chatId": "chat-metrics", "focusId": "focus-metrics"}, "assets": ["https://cdn.oreateai.com/static/result/metrics.mp4"]},
            status="completed",
            request_id="req-done",
            actual_point_cost=3,
            finished_at=now,
        )

        metrics = self.client.get("/metrics")
        self.assertEqual(metrics.status_code, 200)
        payload = metrics.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["accounts"]["healthy"], 1)
        self.assertEqual(payload["tasks"]["queued"], 1)
        self.assertEqual(payload["tasks"]["failed"], 1)
        self.assertEqual(payload["tasks"]["completed"], 1)
        self.assertEqual(payload["tasks"]["queue_length"], 1)

    def test_readyz_requires_schedulable_verified_or_active_account(self):
        new_id = self.seed_account_with_capabilities("new@example.com")
        disabled_id = self.seed_account_with_capabilities("disabled@example.com")
        invalid_id = self.seed_account_with_capabilities("invalid@example.com")
        cooling_id = self.seed_account_with_capabilities("cooling@example.com")
        low_capability_id = self.seed_account_with_capabilities("low-cap@example.com")
        empty_balance_id = self.seed_account_with_capabilities("empty-balance@example.com")
        now = time.time()
        conn = server.db_conn()
        conn.execute("UPDATE accounts SET status='new' WHERE id=?", (new_id,))
        conn.execute("UPDATE accounts SET status='disabled' WHERE id=?", (disabled_id,))
        conn.execute("UPDATE accounts SET status='invalid', last_error=? WHERE id=?", ("212361: risk control", invalid_id))
        conn.execute("UPDATE accounts SET cooldown_until=?, failure_count=? WHERE id=?", (now + 300, 1, cooling_id))
        conn.execute("UPDATE accounts SET model_info_json='{}', video_info_json='{}' WHERE id=?", (low_capability_id,))
        conn.execute(
            "UPDATE accounts SET rest_point=0, daily_point=0, bonus_point=0, balance_updated_at=? WHERE id=?",
            (now, empty_balance_id),
        )
        conn.commit()
        conn.close()

        not_ready = self.client.get("/readyz")
        self.assertEqual(not_ready.status_code, 503)

        active_id = self.seed_account_with_capabilities("active@example.com")
        conn = server.db_conn()
        conn.execute("UPDATE accounts SET status='active' WHERE id=?", (active_id,))
        conn.commit()
        conn.close()

        ready = self.client.get("/readyz")
        self.assertEqual(ready.status_code, 200)
        self.assertTrue(ready.json()["ok"])
        self.assertGreaterEqual(ready.json()["healthy_accounts"], 1)

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

    def test_admin_can_create_customer_key_with_limits_and_update_its_identity(self):
        response = self.client.post(
            "/api/admin/apikeys",
            headers=self.admin_headers(),
            json={
                "name": "上海演示客户",
                "rate_limit_per_minute": 7,
                "daily_request_limit": 80,
                "daily_point_limit": 1200,
                "enabled": False,
            },
        )

        self.assertEqual(response.status_code, 200)
        item = response.json()["item"]
        self.assertEqual(item["name"], "上海演示客户")
        self.assertEqual(item["rate_limit_per_minute"], 7)
        self.assertEqual(item["daily_request_limit"], 80)
        self.assertEqual(item["daily_point_limit"], 1200)
        self.assertEqual(item["status"], "disabled")

        updated = self.client.patch(
            f"/api/admin/apikeys/{item['id']}",
            headers=self.admin_headers(),
            json={"name": "上海正式客户", "enabled": True},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["item"]["name"], "上海正式客户")
        self.assertEqual(updated.json()["item"]["status"], "enabled")

    def test_admin_can_copy_a_key_through_an_authenticated_secret_endpoint(self):
        created_response = self.client.post(
            "/api/admin/apikeys",
            headers=self.admin_headers(),
            json={"name": "复制测试客户"},
        )
        self.assertEqual(created_response.status_code, 200)
        created = created_response.json()["item"]

        unauthorized = self.client.get(f"/api/admin/apikeys/{created['id']}/secret")
        self.assertEqual(unauthorized.status_code, 401)

        revealed = self.client.get(
            f"/api/admin/apikeys/{created['id']}/secret",
            headers=self.admin_headers(),
        )
        self.assertEqual(revealed.status_code, 200)
        self.assertEqual(revealed.json()["key"], created["key"])
        self.assertEqual(revealed.json()["id"], created["id"])

        listed = self.client.get("/api/admin/apikeys", headers=self.admin_headers())
        listed_item = next(value for value in listed.json()["items"] if value["id"] == created["id"])
        self.assertNotIn("key", listed_item)

    def test_api_key_scope_columns_exist(self):
        conn = server.db_conn()
        api_key_cols = {r["name"] for r in conn.execute("PRAGMA table_info(api_keys)").fetchall()}
        conn.close()

        self.assertIn("allowed_kinds", api_key_cols)
        self.assertIn("allowed_models", api_key_cols)
        self.assertIn("allowed_scenes", api_key_cols)
        self.assertIn("allow_uploads", api_key_cols)
        self.assertIn("allow_experimental", api_key_cols)
        self.assertIn("allowed_resolutions", api_key_cols)
        self.assertIn("allowed_durations", api_key_cols)

    def test_api_key_lifecycle_columns_exist(self):
        conn = server.db_conn()
        api_key_cols = {r["name"] for r in conn.execute("PRAGMA table_info(api_keys)").fetchall()}
        conn.close()

        self.assertIn("expires_at", api_key_cols)
        self.assertIn("rotated_from_id", api_key_cols)
        self.assertIn("rotation_note", api_key_cols)

    def test_expired_api_key_is_rejected(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("expired-key")
        conn = server.db_conn()
        conn.execute("UPDATE api_keys SET expires_at=? WHERE key=?", (time.time() - 1, "expired-key"))
        conn.commit()
        conn.close()

        native = self.client.post(
            "/v1/generate",
            headers={"Authorization": "Bearer expired-key"},
            json=self.valid_image_request(),
        )
        self.assertEqual(native.status_code, 401)
        self.assertEqual(native.json()["error"]["code"], "UNAUTHORIZED")

        compat = self.client.get("/v1/models", headers={"Authorization": "Bearer expired-key"})
        self.assertEqual(compat.status_code, 401)
        self.assertIn("error", compat.json())

    def test_api_key_plaintext_is_only_returned_on_create(self):
        create_response = self.client.post(
            "/api/admin/apikeys",
            headers=self.admin_headers(),
            json={"name": "lifecycle-key", "expires_at": time.time() + 3600, "rotation_note": "initial issue"},
        )
        self.assertEqual(create_response.status_code, 200)
        created = create_response.json()["item"]
        key_id = created["id"]
        self.assertIn("key", created)
        self.assertTrue(created["key"].startswith("oreate_"))
        self.assertEqual(created["rotation_note"], "initial issue")
        self.assertGreater(created["expires_at"], time.time())

        listed = self.client.get("/api/admin/apikeys", headers=self.admin_headers())
        self.assertEqual(listed.status_code, 200)
        listed_item = next(item for item in listed.json()["items"] if item["id"] == key_id)
        self.assertNotIn("key", listed_item)
        self.assertIn("key_preview", listed_item)

        updated = self.client.patch(
            f"/api/admin/apikeys/{key_id}",
            headers=self.admin_headers(),
            json={"disabled_reason": "rotated", "rotation_note": "replaced by next key"},
        )
        self.assertEqual(updated.status_code, 200)
        updated_item = updated.json()["item"]
        self.assertNotIn("key", updated_item)
        self.assertEqual(updated_item["disabled_reason"], "rotated")
        self.assertEqual(updated_item["rotation_note"], "replaced by next key")

    def test_admin_can_update_api_key_scope_policy(self):
        key_id = self.seed_api_key("scoped-policy-key")

        response = self.client.patch(
            f"/api/admin/apikeys/{key_id}",
            headers=self.admin_headers(),
            json={
                "allowed_kinds": ["video"],
                "allowed_models": ["Seedance 2.0 Mini"],
                "allowed_scenes": ["text_or_image"],
                "allow_uploads": False,
                "allow_experimental": False,
                "allowed_resolutions": ["480"],
                "allowed_durations": [5],
            },
        )

        self.assertEqual(response.status_code, 200)
        item = response.json()["item"]
        self.assertEqual(item["allowed_kinds"], ["video"])
        self.assertEqual(item["allowed_models"], ["Seedance 2.0 Mini"])
        self.assertEqual(item["allowed_scenes"], ["text_or_image"])
        self.assertFalse(item["allow_uploads"])
        self.assertFalse(item["allow_experimental"])
        self.assertEqual(item["allowed_resolutions"], ["480"])
        self.assertEqual(item["allowed_durations"], [5])

    def test_generate_rejects_request_outside_api_key_scope(self):
        self.seed_account_with_capabilities()
        key_id = self.seed_api_key("scoped-generate-key")
        cases = [
            (
                {"allowed_kinds": ["video"]},
                self.valid_image_request(),
                "API_KEY_KIND_FORBIDDEN",
            ),
            (
                {"allowed_kinds": ["video"], "allowed_models": ["Different Model"]},
                self.valid_video_request(),
                "API_KEY_MODEL_FORBIDDEN",
            ),
            (
                {"allowed_kinds": ["video"], "allowed_models": ["Seedance 2.0 Mini"], "allowed_scenes": ["reference"]},
                self.valid_video_request(),
                "API_KEY_SCENE_FORBIDDEN",
            ),
            (
                {
                    "allowed_kinds": ["video"],
                    "allowed_models": ["Seedance 2.0 Mini"],
                    "allowed_scenes": ["text_or_image"],
                    "allowed_resolutions": ["720"],
                },
                self.valid_video_request(),
                "API_KEY_RESOLUTION_FORBIDDEN",
            ),
            (
                {
                    "allowed_kinds": ["video"],
                    "allowed_models": ["Seedance 2.0 Mini"],
                    "allowed_scenes": ["text_or_image"],
                    "allowed_resolutions": ["480"],
                    "allowed_durations": [10],
                },
                self.valid_video_request(),
                "API_KEY_DURATION_FORBIDDEN",
            ),
        ]

        for policy, body, expected_code in cases:
            patch_response = self.client.patch(
                f"/api/admin/apikeys/{key_id}",
                headers=self.admin_headers(),
                json=policy,
            )
            self.assertEqual(patch_response.status_code, 200)
            response = self.client.post("/v1/generate", headers={"Authorization": "Bearer scoped-generate-key"}, json=body)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["error"]["code"], expected_code)

    def test_upload_rejects_when_api_key_disallows_uploads(self):
        self.seed_account_with_capabilities()
        key_id = self.seed_api_key("scoped-upload-key")
        patch_response = self.client.patch(
            f"/api/admin/apikeys/{key_id}",
            headers=self.admin_headers(),
            json={
                "allowed_kinds": ["video"],
                "allow_uploads": False,
            },
        )
        self.assertEqual(patch_response.status_code, 200)

        with patch.object(
            server.CLIENT,
            "upload_file_bytes",
            return_value={
                "fileName": "sample",
                "fileExt": "png",
                "originSize": 4,
                "object": "uploads/sample.png",
                "status": "completed",
            },
        ):
            response = self.client.post(
                "/v1/uploads",
                headers={"Authorization": "Bearer scoped-upload-key"},
                files={"file": ("sample.png", b"data", "image/png")},
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "API_KEY_UPLOAD_FORBIDDEN")

    def test_admin_can_create_client_and_bind_api_key(self):
        client_response = self.client.post(
            "/api/admin/clients",
            headers=self.admin_headers(),
            json={"name": "Acme", "contact": "ops@acme.test"},
        )
        self.assertEqual(client_response.status_code, 200)
        client_item = client_response.json()["item"]
        self.assertEqual(client_item["name"], "Acme")
        self.assertEqual(client_item["contact"], "ops@acme.test")

        key_response = self.client.post(
            "/api/admin/apikeys",
            headers=self.admin_headers(),
            json={"name": "acme-key", "client_id": client_item["id"]},
        )
        self.assertEqual(key_response.status_code, 200)
        key_item = key_response.json()["item"]
        self.assertEqual(key_item["client_id"], client_item["id"])
        self.assertEqual(key_item["client_name"], "Acme")

        keys = self.client.get("/api/admin/apikeys", headers=self.admin_headers()).json()["items"]
        self.assertEqual(keys[0]["client_name"], "Acme")
        clients = self.client.get("/api/admin/clients", headers=self.admin_headers()).json()["items"]
        self.assertEqual(clients[0]["name"], "Acme")

    def test_admin_cost_report_aggregates_by_customer_key_account_model_and_status(self):
        account_id = self.seed_account_with_capabilities("billing@example.com")
        now = time.time()
        conn = server.db_conn()
        conn.execute("INSERT INTO clients(name,contact,status,created_at) VALUES(?,?,?,?)", ("Acme", "ops@acme.test", "active", now))
        client_id = conn.execute("SELECT id FROM clients WHERE name='Acme'").fetchone()[0]
        conn.execute("INSERT INTO api_keys(client_id,key,name,enabled,created_at) VALUES(?,?,?,?,?)", (client_id, "billing-key", "billing-key", 1, now))
        api_key_id = conn.execute("SELECT id FROM api_keys WHERE key='billing-key'").fetchone()[0]
        conn.executemany(
            """
            INSERT INTO usage_log(
                api_key_id,task_id,kind,account_id,prompt,status,response_summary,actual_point_cost,request_id,idempotency_key,
                model_name,resolution,ratio,duration,scene_id,estimated_point_cost,error_code,status_code,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    api_key_id,
                    None,
                    "video",
                    account_id,
                    "success row",
                    "completed",
                    "",
                    8,
                    "req-success",
                    "",
                    "Seedance 2.0 Mini",
                    "480",
                    "16:9",
                    5,
                    "text_or_image",
                    10,
                    "",
                    200,
                    now,
                ),
                (
                    api_key_id,
                    None,
                    "video",
                    account_id,
                    "failed charged row",
                    "failed",
                    "",
                    3,
                    "req-failed",
                    "",
                    "Seedance 2.0 Mini",
                    "480",
                    "16:9",
                    5,
                    "text_or_image",
                    10,
                    "100003",
                    503,
                    now,
                ),
            ],
        )
        conn.commit()
        conn.close()

        response = self.client.get("/api/admin/cost-report", headers=self.admin_headers())

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["items"])
        row = payload["items"][0]
        self.assertEqual(row["client_name"], "Acme")
        self.assertEqual(row["api_key_name"], "billing-key")
        self.assertEqual(row["account_email"], "billing@example.com")
        self.assertEqual(row["model_name"], "Seedance 2.0 Mini")
        self.assertEqual(row["request_count"], 2)
        self.assertEqual(row["estimated_point_cost"], 20)
        self.assertEqual(row["actual_point_cost"], 11)
        self.assertEqual(row["success_actual_point_cost"], 8)
        self.assertEqual(row["failed_actual_point_cost"], 3)

    def test_admin_tasks_support_limit_offset_and_status_filter(self):
        account_id = self.seed_account_with_capabilities("tasks-page@example.com")
        task_ids = [
            server.save_task(account_id, "image", "queued task", self.valid_image_request(), {"status": "queued"}, status="queued"),
            server.save_task(account_id, "video", "failed task", self.valid_video_request(), {"status": "failed"}, status="failed"),
            server.save_task(account_id, "image", "completed task", self.valid_image_request(), {"status": "completed"}, status="completed"),
        ]

        page = self.client.get("/api/tasks?limit=2&offset=1", headers=self.admin_headers())
        self.assertEqual(page.status_code, 200)
        payload = page.json()
        self.assertEqual(payload["limit"], 2)
        self.assertEqual(payload["offset"], 1)
        self.assertEqual(payload["total"], 3)
        self.assertEqual(len(payload["items"]), 2)
        self.assertEqual([item["id"] for item in payload["items"]], list(reversed(task_ids))[1:3])
        self.assertFalse(payload["has_more"])

        filtered = self.client.get("/api/tasks?status=failed&limit=10&offset=0", headers=self.admin_headers())
        self.assertEqual(filtered.status_code, 200)
        filtered_payload = filtered.json()
        self.assertEqual(filtered_payload["total"], 1)
        self.assertEqual(len(filtered_payload["items"]), 1)
        self.assertEqual(filtered_payload["items"][0]["status"], "failed")
        self.assertEqual(filtered_payload["items"][0]["id"], task_ids[1])

        beyond_filtered_page = self.client.get("/api/tasks?status=failed&limit=1&offset=1", headers=self.admin_headers())
        self.assertEqual(beyond_filtered_page.status_code, 200)
        self.assertEqual(beyond_filtered_page.json()["total"], 1)
        self.assertEqual(beyond_filtered_page.json()["items"], [])
        self.assertFalse(beyond_filtered_page.json()["has_more"])

    def test_admin_tasks_support_full_operational_filters(self):
        first_account_id = self.seed_account_with_capabilities("tasks-filter-a@example.com")
        second_account_id = self.seed_account_with_capabilities("tasks-filter-b@example.com")
        now = time.time()
        report_date = time.strftime("%Y-%m-%d", time.localtime(now))
        conn = server.db_conn()
        conn.execute("INSERT INTO clients(name,contact,status,created_at) VALUES(?,?,?,?)", ("Filter Client A", "", "active", now))
        first_client_id = conn.execute("SELECT id FROM clients WHERE name='Filter Client A'").fetchone()[0]
        conn.execute("INSERT INTO clients(name,contact,status,created_at) VALUES(?,?,?,?)", ("Filter Client B", "", "active", now))
        second_client_id = conn.execute("SELECT id FROM clients WHERE name='Filter Client B'").fetchone()[0]
        conn.execute("INSERT INTO api_keys(client_id,key,name,enabled,created_at) VALUES(?,?,?,?,?)", (first_client_id, "task-filter-key-a", "task-key-a", 1, now))
        conn.execute("INSERT INTO api_keys(client_id,key,name,enabled,created_at) VALUES(?,?,?,?,?)", (second_client_id, "task-filter-key-b", "task-key-b", 1, now))
        first_key_id = conn.execute("SELECT id FROM api_keys WHERE key='task-filter-key-a'").fetchone()[0]
        second_key_id = conn.execute("SELECT id FROM api_keys WHERE key='task-filter-key-b'").fetchone()[0]
        conn.commit()
        conn.close()
        target_id = server.save_task(
            first_account_id,
            "video",
            "target failed video",
            self.valid_video_request(),
            {"status": "failed"},
            status="failed",
            api_key_id=first_key_id,
            model_name="Seedance 2.0 Mini",
            scene_id="text_or_image",
            resolution="480",
            ratio="16:9",
            duration=5,
            error_code="UPSTREAM_ERROR",
        )
        server.save_task(
            second_account_id,
            "image",
            "other completed image",
            self.valid_image_request(),
            {"status": "completed"},
            status="completed",
            api_key_id=second_key_id,
            model_name="Google Nano Banana 2",
            resolution="4K",
            ratio="16:9",
        )

        filtered = self.client.get(
            "/api/tasks"
            f"?client_id={first_client_id}&api_key_id={first_key_id}&account_id={first_account_id}"
            "&kind=video&status=failed&model_name=Seedance+2.0+Mini&scene_id=text_or_image"
            f"&error_code=UPSTREAM_ERROR&date_from={report_date}&date_to={report_date}&limit=10&offset=0",
            headers=self.admin_headers(),
        )

        self.assertEqual(filtered.status_code, 200)
        payload = filtered.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(len(payload["items"]), 1)
        item = payload["items"][0]
        self.assertEqual(item["id"], target_id)
        self.assertEqual(item["account_email"], "tasks-filter-a@example.com")
        self.assertEqual(item["api_key_name"], "task-key-a")
        self.assertEqual(item["client_name"], "Filter Client A")

        bad_date = self.client.get("/api/tasks?date_from=not-a-date", headers=self.admin_headers())
        self.assertEqual(bad_date.status_code, 400)

    def test_gateway_tasks_support_limit_offset_and_status_filter(self):
        self.seed_account_with_capabilities("tenant-a@example.com")
        self.seed_api_key("tenant-a-key")
        self.seed_account_with_capabilities("tenant-b@example.com")
        self.seed_api_key("tenant-b-key")
        conn = server.db_conn()
        tenant_a_id = conn.execute("SELECT id FROM api_keys WHERE key=?", ("tenant-a-key",)).fetchone()[0]
        tenant_b_id = conn.execute("SELECT id FROM api_keys WHERE key=?", ("tenant-b-key",)).fetchone()[0]
        conn.close()
        first = server.save_task(1, "image", "tenant-a queued", self.valid_image_request(), {"status": "queued"}, status="queued", api_key_id=tenant_a_id)
        second = server.save_task(1, "video", "tenant-a failed", self.valid_video_request(), {"status": "failed"}, status="failed", api_key_id=tenant_a_id)
        server.save_task(1, "image", "tenant-b completed", self.valid_image_request(), {"status": "completed"}, status="completed", api_key_id=tenant_b_id)

        page = self.client.get("/v1/tasks?limit=1&offset=0", headers={"Authorization": "Bearer tenant-a-key"})
        self.assertEqual(page.status_code, 200)
        payload = page.json()
        self.assertEqual(payload["limit"], 1)
        self.assertEqual(payload["offset"], 0)
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["id"], second)
        self.assertTrue(payload["has_more"])

        filtered = self.client.get("/v1/tasks?status=queued&kind=image&limit=10&offset=0", headers={"Authorization": "Bearer tenant-a-key"})
        self.assertEqual(filtered.status_code, 200)
        filtered_payload = filtered.json()
        self.assertEqual(len(filtered_payload["items"]), 1)
        self.assertEqual(filtered_payload["items"][0]["id"], first)
        self.assertEqual(filtered_payload["items"][0]["status"], "queued")

        invalid = self.client.get("/v1/tasks?limit=0", headers={"Authorization": "Bearer tenant-a-key"})
        self.assertEqual(invalid.status_code, 422)

    def test_admin_usage_supports_limit_offset_and_filters(self):
        first_account_id = self.seed_account_with_capabilities("usage-a@example.com")
        second_account_id = self.seed_account_with_capabilities("usage-b@example.com")
        now = time.time()
        conn = server.db_conn()
        conn.execute("INSERT INTO api_keys(key,name,enabled,created_at) VALUES(?,?,1,?)", ("usage-key-a", "usage-a", now))
        conn.execute("INSERT INTO api_keys(key,name,enabled,created_at) VALUES(?,?,1,?)", ("usage-key-b", "usage-b", now))
        key_a_id = conn.execute("SELECT id FROM api_keys WHERE key='usage-key-a'").fetchone()[0]
        key_b_id = conn.execute("SELECT id FROM api_keys WHERE key='usage-key-b'").fetchone()[0]
        conn.executemany(
            """
            INSERT INTO usage_log(
                api_key_id,task_id,kind,account_id,prompt,status,response_summary,actual_point_cost,
                request_id,idempotency_key,model_name,resolution,ratio,duration,scene_id,
                estimated_point_cost,error_code,status_code,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (key_a_id, None, "image", first_account_id, "usage-1", "queued", "", 0, "req-1", "", "Provider Image", "1K", "1:1", None, "", 5, "", 202, now - 3),
                (key_b_id, None, "video", second_account_id, "usage-2", "failed", "", 2, "req-2", "", "Provider Video", "720", "16:9", 5, "text_or_image", 10, "UPSTREAM_ERROR", 503, now - 2),
                (key_a_id, None, "video", first_account_id, "usage-3", "completed", "", 8, "req-3", "", "Provider Video", "720", "16:9", 5, "text_or_image", 10, "", 200, now - 1),
            ],
        )
        conn.commit()
        conn.close()

        page = self.client.get("/api/admin/usage?limit=2&offset=1", headers=self.admin_headers())
        self.assertEqual(page.status_code, 200)
        payload = page.json()
        self.assertEqual(payload["limit"], 2)
        self.assertEqual(payload["offset"], 1)
        self.assertEqual(payload["total"], 3)
        self.assertEqual(len(payload["items"]), 2)
        self.assertTrue(payload["has_more"] is False)
        self.assertEqual([item["request_id"] for item in payload["items"]], ["req-2", "req-1"])

        filtered = self.client.get(
            f"/api/admin/usage?api_key_id={key_a_id}&account_id={first_account_id}&kind=video&status=completed&limit=10&offset=0",
            headers=self.admin_headers(),
        )
        self.assertEqual(filtered.status_code, 200)
        filtered_payload = filtered.json()
        self.assertEqual(filtered_payload["total"], 1)
        self.assertEqual(len(filtered_payload["items"]), 1)
        self.assertEqual(filtered_payload["items"][0]["request_id"], "req-3")
        self.assertEqual(filtered_payload["items"][0]["account_email"], "usage-a@example.com")

        bad_limit = self.client.get("/api/admin/usage?limit=201", headers=self.admin_headers())
        self.assertEqual(bad_limit.status_code, 422)
        bad_offset = self.client.get("/api/admin/usage?offset=10001", headers=self.admin_headers())
        self.assertEqual(bad_offset.status_code, 422)
        bad_kind = self.client.get("/api/admin/usage?kind=nope", headers=self.admin_headers())
        self.assertEqual(bad_kind.status_code, 422)

    def test_admin_usage_supports_full_operational_filters(self):
        account_id = self.seed_account_with_capabilities("usage-filter@example.com")
        other_account_id = self.seed_account_with_capabilities("usage-filter-other@example.com")
        now = time.time()
        report_date = time.strftime("%Y-%m-%d", time.localtime(now))
        conn = server.db_conn()
        conn.execute("INSERT INTO clients(name,contact,status,created_at) VALUES(?,?,?,?)", ("Usage Client", "", "active", now))
        client_id = conn.execute("SELECT id FROM clients WHERE name='Usage Client'").fetchone()[0]
        conn.execute("INSERT INTO clients(name,contact,status,created_at) VALUES(?,?,?,?)", ("Other Usage Client", "", "active", now))
        other_client_id = conn.execute("SELECT id FROM clients WHERE name='Other Usage Client'").fetchone()[0]
        conn.execute("INSERT INTO api_keys(client_id,key,name,enabled,created_at) VALUES(?,?,?,?,?)", (client_id, "usage-filter-key", "usage-filter-key", 1, now))
        conn.execute("INSERT INTO api_keys(client_id,key,name,enabled,created_at) VALUES(?,?,?,?,?)", (other_client_id, "usage-filter-other-key", "usage-filter-other-key", 1, now))
        api_key_id = conn.execute("SELECT id FROM api_keys WHERE key='usage-filter-key'").fetchone()[0]
        other_api_key_id = conn.execute("SELECT id FROM api_keys WHERE key='usage-filter-other-key'").fetchone()[0]
        conn.executemany(
            """
            INSERT INTO usage_log(
                api_key_id,task_id,kind,account_id,prompt,status,response_summary,actual_point_cost,
                request_id,idempotency_key,model_name,resolution,ratio,duration,scene_id,
                estimated_point_cost,error_code,status_code,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (api_key_id, None, "video", account_id, "target", "failed", "", 3, "usage-target", "", "Seedance 2.0 Mini", "480", "16:9", 5, "text_or_image", 10, "UPSTREAM_ERROR", 503, now),
                (other_api_key_id, None, "image", other_account_id, "other", "completed", "", 0, "usage-other", "", "Google Nano Banana 2", "4K", "16:9", None, "", 12, "", 200, now),
            ],
        )
        conn.commit()
        conn.close()

        filtered = self.client.get(
            "/api/admin/usage"
            f"?client_id={client_id}&api_key_id={api_key_id}&account_id={account_id}"
            "&kind=video&status=failed&model_name=Seedance+2.0+Mini&error_code=UPSTREAM_ERROR"
            f"&date_from={report_date}&date_to={report_date}&limit=10&offset=0",
            headers=self.admin_headers(),
        )

        self.assertEqual(filtered.status_code, 200)
        payload = filtered.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(len(payload["items"]), 1)
        item = payload["items"][0]
        self.assertEqual(item["request_id"], "usage-target")
        self.assertEqual(item["account_email"], "usage-filter@example.com")
        self.assertEqual(item["api_key_name"], "usage-filter-key")
        self.assertEqual(item["client_name"], "Usage Client")

        bad_date = self.client.get("/api/admin/usage?date_to=bad-date", headers=self.admin_headers())
        self.assertEqual(bad_date.status_code, 400)

    def test_admin_uploads_support_listing_filters_and_sanitized_attachments(self):
        account_id = self.seed_account_with_capabilities("upload-admin@example.com")
        other_account_id = self.seed_account_with_capabilities("upload-admin-other@example.com")
        now = time.time()
        report_date = time.strftime("%Y-%m-%d", time.localtime(now))
        conn = server.db_conn()
        conn.execute("INSERT INTO clients(name,contact,status,created_at) VALUES(?,?,?,?)", ("Upload Client", "", "active", now))
        client_id = conn.execute("SELECT id FROM clients WHERE name='Upload Client'").fetchone()[0]
        conn.execute("INSERT INTO api_keys(client_id,key,name,enabled,created_at) VALUES(?,?,?,?,?)", (client_id, "upload-admin-key", "upload-admin-key", 1, now))
        conn.execute("INSERT INTO api_keys(key,name,enabled,created_at) VALUES(?,?,1,?)", ("upload-admin-other-key", "upload-admin-other-key", now))
        api_key_id = conn.execute("SELECT id FROM api_keys WHERE key='upload-admin-key'").fetchone()[0]
        other_api_key_id = conn.execute("SELECT id FROM api_keys WHERE key='upload-admin-other-key'").fetchone()[0]
        conn.commit()
        conn.close()
        attachment = {
            "fileName": "admin-upload",
            "fileExt": "png",
            "contentType": "image/png",
            "originSize": 1234,
            "object": "uploads/admin-upload.png",
            "status": "completed",
            "sessionkey": "temporary-upload-session",
            "cookies": {"OUID": "upload-ouid", "ouss": "upload-ouss"},
        }
        server.save_uploaded_media_record(api_key_id, account_id, attachment)
        server.save_uploaded_media_record(
            other_api_key_id,
            other_account_id,
            {
                "fileName": "other-upload",
                "fileExt": "mp4",
                "contentType": "video/mp4",
                "originSize": 4567,
                "object": "uploads/other-upload.mp4",
                "status": "completed",
            },
        )
        server.save_task(
            account_id,
            "video",
            "uses upload",
            {**self.valid_video_request(), "image": attachment},
            {"status": "queued"},
            status="queued",
            api_key_id=api_key_id,
            scene_id="text_or_image",
        )

        page = self.client.get("/api/admin/uploads?limit=1&offset=0", headers=self.admin_headers())
        self.assertEqual(page.status_code, 200)
        page_payload = page.json()
        self.assertEqual(page_payload["total"], 2)
        self.assertEqual(len(page_payload["items"]), 1)
        self.assertTrue(page_payload["has_more"])

        filtered = self.client.get(
            "/api/admin/uploads"
            f"?api_key_id={api_key_id}&account_id={account_id}&kind=image&status=completed"
            f"&date_from={report_date}&date_to={report_date}&limit=10&offset=0",
            headers=self.admin_headers(),
        )

        self.assertEqual(filtered.status_code, 200)
        payload = filtered.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(len(payload["items"]), 1)
        item = payload["items"][0]
        self.assertEqual(item["object_path"], "uploads/admin-upload.png")
        self.assertEqual(item["kind"], "image")
        self.assertEqual(item["account_email"], "upload-admin@example.com")
        self.assertEqual(item["api_key_name"], "upload-admin-key")
        self.assertEqual(item["client_name"], "Upload Client")
        self.assertEqual(item["related_task_count"], 1)
        body_text = json.dumps(item, ensure_ascii=False)
        self.assertNotIn("temporary-upload-session", body_text)
        self.assertNotIn("upload-ouid", body_text)
        self.assertNotIn("upload-ouss", body_text)

        bad_kind = self.client.get("/api/admin/uploads?kind=archive", headers=self.admin_headers())
        self.assertEqual(bad_kind.status_code, 422)

    def test_admin_can_patch_scene_policy_and_reflect_in_capabilities(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("scene-policy-key")
        original_cfg = json.loads(json.dumps(server.CFG))
        try:
            response = self.client.patch(
                "/api/video-scenes/reference/policy",
                headers=self.admin_headers(),
                json={
                    "enabled": True,
                    "experimental": True,
                    "verification_status": "unit_tested",
                    "risk_level": "medium",
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["policy"]["enabled"])

            caps = self.client.get("/v1/capabilities", headers={"Authorization": "Bearer scene-policy-key"})
            self.assertEqual(caps.status_code, 200)
            scenes = {scene["scene_id"]: scene for scene in caps.json()["video"]["scenes"]}
            self.assertTrue(scenes["reference"]["enabled"])
            self.assertEqual(scenes["reference"]["verification_status"], "unit_tested")
            self.assertEqual(scenes["reference"]["risk_level"], "medium")
        finally:
            server.CFG = original_cfg

    def test_admin_can_patch_model_policy_and_disable_generation(self):
        self.seed_account_with_capabilities()
        self.seed_api_key("model-policy-key")
        original_cfg = json.loads(json.dumps(server.CFG))
        try:
            response = self.client.patch(
                f"/api/models/{server.quote('Seedance 2.0 Mini', safe='')}/policy",
                headers=self.admin_headers(),
                json={
                    "enabled": False,
                    "experimental": True,
                    "verification_status": "disabled",
                    "risk_level": "high",
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()["policy"]["enabled"])

            with (
                patch.object(server.CLIENT, "session_from_account", return_value=object()),
                patch.object(server.CLIENT, "create_chat_session", return_value={"chatId": "chat-policy"}) as create_chat,
            ):
                generated = self.client.post(
                    "/v1/generate",
                    headers={"Authorization": "Bearer model-policy-key"},
                    json=self.valid_video_request(scene_id="text_or_image"),
                )

            self.assertEqual(generated.status_code, 422)
            self.assertEqual(generated.json()["error"]["code"], "MODEL_DISABLED")
            create_chat.assert_not_called()
        finally:
            server.CFG = original_cfg
