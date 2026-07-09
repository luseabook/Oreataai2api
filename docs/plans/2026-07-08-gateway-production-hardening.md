# Gateway Production Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Harden the OreateAI image/video gateway with stable errors, parameter validation, API key policy, idempotency, account scheduling, and auditability.

**Architecture:** Keep the current FastAPI + SQLite deployment shape and add small testable helper functions inside `server.py`. Add schema migrations through `init_db()` using `PRAGMA table_info`, keep existing `/v1/generate` and `/v1/task/{task_id}` compatible, and add stricter behavior only before upstream Oreate calls.

**Tech Stack:** FastAPI, Pydantic, SQLite, unittest, vanilla admin HTML/JS embedded in `server.py`.

---

## Source Documents

- Design source of truth: `docs/specs/2026-07-08-gateway-production-hardening-design.md`
- Existing implemented baseline: `docs/superpowers/specs/2026-07-08-admin-credentials-and-model-capabilities-design.md`
- Current main code: `server.py`
- Current tests: `tests/security_regression_tests.py`

## Task 1: Gateway Error Envelope

**Files:**

- Modify: `server.py`
- Create: `tests/gateway_hardening_tests.py`

**Step 1: Write failing tests**

Add tests that call `/v1/capabilities` and `/v1/generate` without valid credentials and assert the new error envelope for `/v1/*`.

```python
def test_v1_errors_use_stable_envelope(self):
    response = self.client.get("/v1/capabilities")
    self.assertEqual(response.status_code, 401)
    payload = response.json()
    self.assertFalse(payload["ok"])
    self.assertEqual(payload["error"]["code"], "UNAUTHORIZED")
    self.assertIn("request_id", payload)
```

**Step 2: Verify red**

Run:

```bash
python -m unittest tests.gateway_hardening_tests.GatewayHardeningTests.test_v1_errors_use_stable_envelope
```

Expected: FAIL because current FastAPI errors return `{"detail": ...}`.

**Step 3: Implement minimal code**

- Add `GatewayAPIError`.
- Add `gateway_error_response(request_id, status_code, code, message, details=None)`.
- Add FastAPI exception handler for `GatewayAPIError`.
- Update `require_api_key` and `/v1/*` routes to raise gateway errors.
- Generate `request_id` from `X-Request-ID` or `req_` + random token.

**Step 4: Verify green**

Run:

```bash
python -m unittest tests.gateway_hardening_tests.GatewayHardeningTests.test_v1_errors_use_stable_envelope
python -m unittest discover -s tests -p "*_tests.py"
```

Expected: PASS.

## Task 2: Schema Migration For Gateway Policy And Audit

**Files:**

- Modify: `server.py:init_db`
- Test: `tests/gateway_hardening_tests.py`

**Step 1: Write failing tests**

Assert new columns and table exist after `server.init_db()`.

```python
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
    self.assertIn("estimated_point_cost", usage_cols)
    self.assertIsNotNone(idem)
```

**Step 2: Verify red**

Run:

```bash
python -m unittest tests.gateway_hardening_tests.GatewayHardeningTests.test_gateway_hardening_schema_is_migrated
```

Expected: FAIL because columns and table are missing.

**Step 3: Implement minimal migration**

In `init_db()`:

- Add missing `api_keys` columns: `rate_limit_per_minute`, `daily_request_limit`, `daily_point_limit`.
- Add missing `accounts` columns: `last_used_at`, `failure_count`, `cooldown_until`.
- Add missing `usage_log` columns from the design.
- Create `idempotency_keys`.

Use helper:

```python
def add_column_if_missing(conn, table: str, column: str, definition: str) -> None:
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
```

**Step 4: Verify green**

Run:

```bash
python -m unittest tests.gateway_hardening_tests.GatewayHardeningTests.test_gateway_hardening_schema_is_migrated
```

Expected: PASS.

## Task 3: Capability-Based Request Validation

**Files:**

- Modify: `server.py`
- Test: `tests/gateway_hardening_tests.py`

**Step 1: Write failing tests**

Seed an account with model capabilities, create an API key, and assert invalid model/resolution/duration returns `422` without calling `CLIENT.create_chat`.

```python
def test_generate_rejects_invalid_video_options_before_upstream_call(self):
    account_id = self.seed_account_with_capabilities()
    self.seed_api_key("hard-key")
    with patch.object(server.CLIENT, "create_chat") as create_chat:
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
```

**Step 2: Verify red**

Run:

```bash
python -m unittest tests.gateway_hardening_tests.GatewayHardeningTests.test_generate_rejects_invalid_video_options_before_upstream_call
```

Expected: FAIL because current code forwards invalid values.

**Step 3: Implement validation helpers**

Add pure helpers:

- `effective_generation_options(body, caps) -> Dict[str, Any]`
- `validate_generation_options(kind, options, caps) -> None`
- `estimate_point_cost(kind, options, caps) -> Optional[int]`

Validation must:

- Reject missing capability cache with `503 CAPABILITIES_UNAVAILABLE`.
- Reject unsupported model/resolution/ratio/duration/scene with `422`.
- Fill default values from `CFG["oreate"]` before validation.

**Step 4: Verify green**

Run:

```bash
python -m unittest tests.gateway_hardening_tests.GatewayHardeningTests.test_generate_rejects_invalid_video_options_before_upstream_call
python -m unittest discover -s tests -p "*_tests.py"
```

Expected: PASS.

## Task 4: Point Cost Audit

**Files:**

- Modify: `server.py`
- Test: `tests/gateway_hardening_tests.py`

**Step 1: Write failing tests**

Assert valid generation records model parameters and estimated cost in `usage_log`.

```python
def test_generate_records_model_parameters_and_estimated_cost(self):
    account_id = self.seed_account_with_capabilities()
    key_id = self.seed_api_key("cost-key")
    with patch.object(server.CLIENT, "create_chat", return_value={"data": {"chatId": "chat-cost"}}):
        response = self.client.post(
            "/v1/generate",
            headers={"Authorization": "Bearer cost-key"},
            json={"kind": "image", "prompt": "hello", "model_name": "Google Nano Banana 2", "resolution": "4K", "ratio": "16:9"},
        )
    self.assertEqual(response.status_code, 200)
    conn = server.db_conn()
    row = conn.execute("SELECT model_name,resolution,ratio,estimated_point_cost FROM usage_log WHERE api_key_id=?", (key_id,)).fetchone()
    conn.close()
    self.assertEqual(row["model_name"], "Google Nano Banana 2")
    self.assertEqual(row["estimated_point_cost"], 12)
```

**Step 2: Verify red**

Run the single test and expect missing columns or missing values.

**Step 3: Implement audit write**

- Extend `log_usage()` parameters.
- Call `log_usage()` with model options, request ID, idempotency key, status code, error code, estimated point cost.
- Include `estimated_point_cost` in `/v1/generate` response.

**Step 4: Verify green**

Run the single test, then all tests.

## Task 5: Idempotency-Key

**Files:**

- Modify: `server.py`
- Test: `tests/gateway_hardening_tests.py`

**Step 1: Write failing tests**

```python
def test_idempotency_key_replays_same_response_without_second_task(self):
    self.seed_account_with_capabilities()
    self.seed_api_key("idem-key")
    with patch.object(server.CLIENT, "create_chat", return_value={"data": {"chatId": "chat-idem"}}) as create_chat:
        first = self.client.post("/v1/generate", headers={"Authorization": "Bearer idem-key", "Idempotency-Key": "same-1"}, json=self.valid_image_request())
        second = self.client.post("/v1/generate", headers={"Authorization": "Bearer idem-key", "Idempotency-Key": "same-1"}, json=self.valid_image_request())
    self.assertEqual(first.status_code, 200)
    self.assertEqual(second.status_code, 200)
    self.assertTrue(second.json()["idempotent_replay"])
    self.assertEqual(create_chat.call_count, 1)
```

Add another test for same key with different body returning `409 IDEMPOTENCY_KEY_CONFLICT`.

**Step 2: Verify red**

Expected: FAIL because current code creates a second task.

**Step 3: Implement helper flow**

- `request_hash_for_generation(body)`.
- `find_idempotency_record(api_key_id, idempotency_key)`.
- `save_idempotency_record(...)`.
- Check idempotency before account selection and upstream call.
- Save only successful task creation responses.

**Step 4: Verify green**

Run idempotency tests and full suite.

## Task 6: API Key Rate Limit And Daily Quota

**Files:**

- Modify: `server.py`
- Test: `tests/gateway_hardening_tests.py`

**Step 1: Write failing tests**

Add tests for:

- `rate_limit_per_minute=1` rejects second immediate request with `429 RATE_LIMITED`.
- `daily_request_limit=1` rejects second request with `429 DAILY_REQUEST_LIMIT_EXCEEDED`.
- `daily_point_limit=10` rejects a request estimated at 12 points.

**Step 2: Verify red**

Run:

```bash
python -m unittest tests.gateway_hardening_tests.GatewayHardeningTests.test_api_key_rate_limit_rejects_second_request tests.gateway_hardening_tests.GatewayHardeningTests.test_daily_point_limit_blocks_expensive_request
```

Expected: FAIL because there is no policy enforcement.

**Step 3: Implement policy helpers**

- `get_api_key_record(credentials) -> sqlite3.Row`.
- `resolve_api_key_policy(row)`.
- `check_rate_limit(api_key_id, now)`.
- `check_daily_quota(api_key_id, estimated_point_cost, now)`.

Store in-memory rate buckets:

```python
RATE_BUCKETS: Dict[int, List[float]] = {}
```

Keep daily quota based on `usage_log.created_at >= start_of_day`.

**Step 4: Verify green**

Run targeted tests and full suite.

## Task 7: Account Scheduling And Cooldown

**Files:**

- Modify: `server.py`
- Test: `tests/gateway_hardening_tests.py`

**Step 1: Write failing tests**

Add tests:

- Scheduler skips account with `cooldown_until` in the future.
- Scheduler picks account with matching image/video capability.
- Upstream exception increments `failure_count` and sets `cooldown_until`.
- Success updates `last_used_at` and clears failure fields.

**Step 2: Verify red**

Run targeted scheduler tests and expect current `pick_account_for_task` behavior to fail.

**Step 3: Implement scheduler**

Replace `pick_account_for_task(kind)` with:

```python
def pick_account_for_generation(kind: str, options: Dict[str, Any], requested_account_id: Optional[int] = None) -> sqlite3.Row:
    ...
```

Add:

- `account_supports_options(account, kind, options)`.
- `mark_account_success(account_id)`.
- `mark_account_failure(account_id, error)`.

Wrap upstream `CLIENT.create_chat` in `try/except` to mark failure and return `503 UPSTREAM_ERROR`.

**Step 4: Verify green**

Run scheduler tests and full suite.

## Task 8: Task Detail Alias And Audit Response

**Files:**

- Modify: `server.py`
- Test: `tests/gateway_hardening_tests.py`

**Step 1: Write failing tests**

Assert `/v1/tasks/{task_id}` returns the same scoped task detail as `/v1/task/{task_id}` and includes audit fields.

**Step 2: Verify red**

Run targeted test and expect `404` because alias is missing.

**Step 3: Implement alias**

- Extract `gateway_task_detail_payload(task_id, api_key_id)`.
- Route both `/v1/task/{task_id}` and `/v1/tasks/{task_id}` to it.
- Merge `payload_json` fields and `usage_log` audit fields.

**Step 4: Verify green**

Run targeted test and full suite.

## Task 9: Admin API Key Policy UI

**Files:**

- Modify: `server.py` `ADMIN_HTML`
- Test: `tests/gateway_hardening_tests.py`

**Step 1: Write failing HTML tests**

Assert admin HTML contains:

- `rate_limit_per_minute`
- `daily_request_limit`
- `daily_point_limit`
- `updateApiKeyPolicy`
- audit columns for estimated point cost and error code

**Step 2: Verify red**

Run targeted HTML test and expect missing strings.

**Step 3: Implement backend and UI**

- Update `public_api_key()` to include policy fields.
- Add `PATCH /api/admin/apikeys/{key_id}` for policy updates.
- Update API Key table to show editable limit fields.
- Update usage table to display model and cost audit fields.

**Step 4: Verify green**

Run HTML/API tests and full suite.

## Task 10: Documentation And Verification

**Files:**

- Modify: `README.md`
- Modify: `docs/specs/2026-07-08-gateway-production-hardening-design.md` only if implementation differs from design.

**Step 1: Update README**

Document:

- `/v1/generate` `Idempotency-Key`.
- `/v1/tasks/{task_id}`.
- Error envelope.
- API Key limit fields.
- Current result hydration behavior: SSE parsing plus `/oreate/memory/getmessagelist` polling now retrieves real Oreate CDN image/video assets for proven image, text-to-video, and upload-backed `text_or_image` paths.
- Known remaining limitation: advanced upload-backed video scenes `reference`, `frame_based`, and `motion` still need separate live success proof.

**Step 2: Run all verification**

Run:

```bash
python -m unittest discover -s tests -p "*_tests.py"
python -m py_compile server.py banti_token_generator.py
node -e 'const fs=require("fs"); const text=fs.readFileSync("server.py","utf8"); const html=text.match(/ADMIN_HTML = """([\s\S]*?)"""/)[1]; const script=html.match(/<script>([\s\S]*?)<\/script>/)[1]; new Function(script); console.log("js parse ok");'
git diff --check
```

Expected:

- All unit tests pass.
- Python compilation exits `0`.
- JS parse prints `js parse ok`.
- `git diff --check` has no whitespace errors. Windows CRLF warnings are acceptable if present.

**Step 3: Manual smoke**

Run:

```bash
python -c 'from fastapi.testclient import TestClient; import server; server.init_db(); c=TestClient(server.app); print(c.get("/v1/capabilities").status_code); print(c.post("/v1/generate", json={"kind":"image","prompt":"x"}).status_code)'
```

Expected:

```text
401
401
```

## Implementation Order

Implement tasks in order. Do not start Task 3 before Task 1 and Task 2 are green, because validation and quota errors must use the final envelope and schema.

## Review Gates

After each task:

- Run the targeted test.
- Run the full test suite when the task changes shared auth, schema, or generation flow.
- Inspect `git diff -- server.py tests/gateway_hardening_tests.py` for accidental unrelated changes.

Before declaring completion:

- Verify every acceptance criterion in `docs/specs/2026-07-08-gateway-production-hardening-design.md`.
- Run final verification commands from Task 10.
- Confirm no real `config.json`, `accounts.db`, cookies, Oreate credentials, or API keys are included in the diff.
