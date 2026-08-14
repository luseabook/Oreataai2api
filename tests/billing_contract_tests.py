"""Billing-contract regression tests.

These tests lock down the cost-related guarantees that matter for running this
gateway as a charging service:

- A capability combination without a matching cost row must be rejected
  (``POINT_COST_UNAVAILABLE``), never admitted as a zero-cost task.
- Malformed cost data is dropped during normalization and treated as a missing
  cost row, instead of flowing into reservations.
- Automatic account failover preserves the first account's spend and audit
  trail by keeping its usage row and creating a separate row for the retry.
- Actual spend inference falls back to other balance buckets when
  ``rest_point`` is unknown.
"""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import server

TEST_ENCRYPTION_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


class BillingContractTests(unittest.TestCase):
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

    def seed_api_key(self, key, rate_limit_per_minute=None):
        now = time.time()
        conn = server.db_conn()
        conn.execute("INSERT INTO api_keys(key,name,enabled,created_at) VALUES(?,?,1,?)", (key, key, now))
        conn.execute(
            """
            UPDATE api_keys
            SET rate_limit_per_minute=?
            WHERE key=?
            """,
            (rate_limit_per_minute, key),
        )
        conn.commit()
        key_id = conn.execute("SELECT id FROM api_keys WHERE key=?", (key,)).fetchone()[0]
        conn.close()
        return key_id

    def seed_account(self, email, image_info, video_info, rest_point=1000):
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
                json.dumps(image_info),
                json.dumps(video_info),
                rest_point,
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

    def image_info_with_cost(self, point_cost):
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
                                "pointCost": point_cost,
                            }
                        ],
                    }
                ]
            }
        }

    def video_info_with_cost(self, point_cost_image):
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
                            "videoSize": [{"ratio": "16:9"}],
                            "supportAudio": True,
                            "supportModifySize": True,
                            "pointCostImage": point_cost_image,
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

    def post_generate(self, key, body):
        return self.client.post(
            "/v1/generate",
            headers={"Authorization": f"Bearer {key}"},
            json=body,
        )

    def test_video_duration_without_cost_row_is_rejected(self):
        """A supported duration that has no matching cost row must not queue."""
        self.seed_account(
            "priced@example.com",
            {},
            self.video_info_with_cost([{"duration": 5, "resolution": "480", "point": 20}]),
        )
        self.seed_api_key("cost-key", rate_limit_per_minute=60)
        response = self.post_generate(
            "cost-key",
            {
                "kind": "video",
                "prompt": "hello",
                "model_name": "Seedance 2.0 Mini",
                "resolution": "480",
                "ratio": "16:9",
                "duration": 10,
                "scene_id": "text_or_image",
            },
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "POINT_COST_UNAVAILABLE")
        conn = server.db_conn()
        task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        conn.close()
        self.assertEqual(task_count, 0, "task must not be queued without a known cost")

    def test_image_malformed_cost_is_treated_as_missing(self):
        """Malformed point values are dropped; the combination must be rejected."""
        self.seed_account(
            "malformed@example.com",
            self.image_info_with_cost(
                [
                    {"resolution": "2K", "point": "abc"},
                    {"resolution": "4K", "point": -5},
                ]
            ),
            {},
        )
        self.seed_api_key("cost-key-2", rate_limit_per_minute=60)
        response = self.post_generate(
            "cost-key-2",
            {
                "kind": "image",
                "prompt": "hello",
                "model_name": "Google Nano Banana 2",
                "resolution": "4K",
                "ratio": "16:9",
            },
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "POINT_COST_UNAVAILABLE")
        conn = server.db_conn()
        task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        conn.close()
        self.assertEqual(task_count, 0, "task must not be queued with a malformed cost")

    def test_valid_cost_still_admits_and_reserves(self):
        """The happy path still works and reserves the estimated cost."""
        self.seed_account(
            "happy@example.com",
            self.image_info_with_cost([{"resolution": "4K", "point": 12}]),
            {},
            rest_point=100,
        )
        key_id = self.seed_api_key("cost-key-3", rate_limit_per_minute=60)
        response = self.post_generate(
            "cost-key-3",
            {
                "kind": "image",
                "prompt": "hello",
                "model_name": "Google Nano Banana 2",
                "resolution": "4K",
                "ratio": "16:9",
            },
        )
        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["estimated_point_cost"], 12)
        conn = server.db_conn()
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (payload["task_id"],)).fetchone()
        usage = conn.execute(
            "SELECT * FROM usage_log WHERE task_id=? AND api_key_id=?",
            (payload["task_id"], key_id),
        ).fetchone()
        conn.close()
        self.assertEqual(task["estimated_point_cost"], 12)
        self.assertEqual(usage["estimated_point_cost"], 12)

    def test_failover_preserves_first_account_spend(self):
        """Failover must keep the original usage row (with spend) and add a new one."""
        now = time.time()
        conn = server.db_conn()
        conn.execute(
            "INSERT INTO accounts(email,password,status,source,ouid,ouss,rest_point,daily_point,bonus_point,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                "first@example.com",
                server.encrypt_secret_value("p"),
                "verified",
                "manual",
                server.encrypt_secret_value("o1"),
                server.encrypt_secret_value("s1"),
                80,
                0,
                0,
                now,
                now,
            ),
        )
        first_id = conn.execute("SELECT id FROM accounts WHERE email=?", ("first@example.com",)).fetchone()[0]
        conn.execute(
            "INSERT INTO accounts(email,password,status,source,ouid,ouss,rest_point,daily_point,bonus_point,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                "second@example.com",
                server.encrypt_secret_value("p"),
                "verified",
                "manual",
                server.encrypt_secret_value("o2"),
                server.encrypt_secret_value("s2"),
                500,
                0,
                0,
                now,
                now,
            ),
        )
        second_id = conn.execute("SELECT id FROM accounts WHERE email=?", ("second@example.com",)).fetchone()[0]
        conn.commit()
        conn.close()
        key_id = self.seed_api_key("failover-key", rate_limit_per_minute=60)
        conn = server.db_conn()
        conn.execute(
            """
            INSERT INTO tasks(
                api_key_id, account_id, kind, prompt, model_name, scene_id, resolution, ratio,
                duration, estimated_point_cost, request_id, payload_json, response_json, assets_json,
                status, attempt_count, balance_before_json, created_at, updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                key_id,
                first_id,
                "video",
                "hello",
                "Seedance 2.0 Mini",
                "text_or_image",
                "480",
                "16:9",
                5,
                20,
                "req_failover",
                "{}",
                "{}",
                "[]",
                "running",
                1,
                json.dumps({"rest_point": 100, "daily_point": 0, "bonus_point": 0}),
                now,
                now,
            ),
        )
        task_id = conn.execute("SELECT id FROM tasks WHERE request_id='req_failover'").fetchone()[0]
        conn.execute(
            """
            INSERT INTO task_attempts(task_id, attempt_no, phase, account_id, status, started_at)
            VALUES(?,1,'generation',?, 'running', ?)
            """,
            (task_id, first_id, now),
        )
        attempt_id = conn.execute("SELECT id FROM task_attempts WHERE task_id=?", (task_id,)).fetchone()[0]
        conn.execute(
            """
            INSERT INTO usage_log(api_key_id, task_id, kind, account_id, prompt, status, request_id, model_name, scene_id, resolution, ratio, duration, estimated_point_cost, created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                key_id,
                task_id,
                "video",
                first_id,
                "hello",
                "running",
                "req_failover",
                "Seedance 2.0 Mini",
                "text_or_image",
                "480",
                "16:9",
                5,
                20,
                now,
            ),
        )
        conn.commit()
        conn.close()

        task_row = server.fetch_task_row(task_id)
        task = dict(task_row)
        second_account = server.fetch_account_row(second_id)
        error = server.UpstreamGenerationError({"code": "200001", "message": "session expired"})
        options = {
            "model_name": "Seedance 2.0 Mini",
            "scene_id": "text_or_image",
            "resolution": "480",
            "ratio": "16:9",
            "duration": 5,
        }
        with patch.object(
            server,
            "capture_account_balance_snapshot",
            return_value={
                "point_balance_json": {"rest_point": 80, "daily_point": 0, "bonus_point": 0},
                "rest_point": 80,
                "daily_point": 0,
                "bonus_point": 0,
            },
        ):
            scheduled = server.schedule_task_account_failover(
                task,
                attempt_id,
                error,
                second_account,
                options,
                estimated_point_cost=20,
            )
        self.assertTrue(scheduled)

        conn = server.db_conn()
        rows = conn.execute(
            "SELECT * FROM usage_log WHERE task_id=? ORDER BY id ASC",
            (task_id,),
        ).fetchall()
        updated_task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        conn.close()

        self.assertEqual(len(rows), 2, "failover must create a separate usage row")
        first_row = rows[0]
        second_row = rows[1]
        self.assertEqual(first_row["account_id"], first_id)
        self.assertEqual(first_row["status"], "failed")
        self.assertEqual(first_row["error_code"], "200001")
        self.assertEqual(first_row["actual_point_cost"], 20, "first account spend must be preserved")
        self.assertEqual(second_row["account_id"], second_id)
        self.assertEqual(second_row["status"], "queued")
        self.assertEqual(second_row["estimated_point_cost"], 20)
        self.assertEqual(updated_task["account_id"], second_id)
        preserved_before = json.loads(updated_task["balance_before_json"] or "{}")
        self.assertEqual(
            preserved_before.get("rest_point"),
            100,
            "first account balance snapshot must be preserved on the task",
        )
        self.assertIsNone(updated_task["balance_after_json"])

    def test_actual_cost_falls_back_to_other_buckets(self):
        before = {"rest_point": None, "daily_point": 50, "bonus_point": 10}
        after = {"rest_point": None, "daily_point": 40, "bonus_point": 10}
        self.assertEqual(
            server.actual_point_cost_from_balance_snapshots(before, after),
            10,
        )
        # Unknown rest plus an unknown bucket must stay unknown, never undercount.
        self.assertIsNone(
            server.actual_point_cost_from_balance_snapshots(
                {"rest_point": None, "daily_point": 50, "bonus_point": None},
                {"rest_point": None, "daily_point": 40, "bonus_point": None},
            )
        )
        # rest_point delta still wins when present.
        self.assertEqual(
            server.actual_point_cost_from_balance_snapshots(
                {"rest_point": 100, "daily_point": 50, "bonus_point": 10},
                {"rest_point": 80, "daily_point": 60, "bonus_point": 20},
            ),
            20,
        )


if __name__ == "__main__":
    unittest.main()
