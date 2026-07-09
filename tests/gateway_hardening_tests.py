import json
import threading
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
        base_cfg = json.loads(json.dumps(server.CFG))
        self.cfg_patch = patch.object(
            server,
            "CFG",
            server.deep_merge(
                base_cfg,
                {
                    "server": {"host": "127.0.0.1", "admin_username": "admin", "admin_password": "test-admin-password"},
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
                patch.object(server, "save_account", return_value=1),
            ):
                result = server.auto_register_accounts(1)
        finally:
            server.CFG = original_cfg

        self.assertEqual(result[0]["status"], "verified")
        self.assertTrue(visit_link.called)
        self.assertTrue(visit_link.call_args.kwargs["verify"])

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

    def test_media_upload_uses_web_ai_image_source_and_convert_submit(self):
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
            attachment = client.upload_file_bytes(fake_session, "ref.mp4", b"data", "video/mp4")

        token_payload = fake_session.posts[0][1]["json"]
        convert_payload = fake_session.posts[1][1]["json"]
        self.assertEqual(token_payload["source"], "aiImage")
        self.assertEqual(convert_payload["object"], "uploads/ref.mp4")
        self.assertEqual(convert_payload["fileName"], "ref.mp4")
        self.assertEqual(attachment["docId"], "doc-video")
        self.assertEqual(attachment["parseInfo"], {"type": "media"})

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

    def test_risk_control_error_cools_account_without_invalidating_it(self):
        account_id = self.seed_account_with_capabilities()

        server.mark_account_failure(
            account_id,
            server.UpstreamGenerationError({"code": "212361", "message": "risk control"}),
        )

        conn = server.db_conn()
        row = conn.execute("SELECT status,failure_count,cooldown_until,last_error FROM accounts WHERE id=?", (account_id,)).fetchone()
        conn.close()
        self.assertEqual(row["status"], "verified")
        self.assertEqual(row["failure_count"], 1)
        self.assertGreater(row["cooldown_until"], time.time())
        self.assertIn("212361", row["last_error"])

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
        self.assertIn("updateApiKeyPolicy", html)
        self.assertIn("estimated_point_cost", html)
        self.assertIn("actual_point_cost", html)
        self.assertIn("error_code", html)
        self.assertIn("createClient", html)
        self.assertIn("client_id", html)
        self.assertIn("loadAuditLogs", html)
        self.assertIn("audit-tbody", html)
        self.assertIn("/api/admin/logout", html)
        self.assertIn("/api/admin/audit-logs", html)
        self.assertIn("downloadBackup", html)
        self.assertIn("restoreBackup", html)
        self.assertIn("/api/admin/backup", html)
        self.assertIn("/api/admin/restore", html)

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
        self.assertEqual(items["risk@example.com"]["risk_status"], "risk_control")

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
        self.assertEqual(payload["risk_control_accounts"], 1)

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
