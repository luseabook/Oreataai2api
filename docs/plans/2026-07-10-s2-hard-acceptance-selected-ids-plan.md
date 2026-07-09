# S2 Hard Acceptance Selected IDs Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the highest-impact S2 blockers in this round by implementing `P0-02`, `P3-02`, and `P5-04` with test-first evidence and updated acceptance records.

**Architecture:** Keep the current single-file FastAPI service structure, extend the SQLite schema minimally, and add narrowly-scoped helpers for sensitive-field encryption, API key capability enforcement, and cost aggregation. Avoid large refactors; only touch the code paths that map directly to the selected acceptance IDs and their tests/docs.

**Tech Stack:** Python, FastAPI, SQLite, unittest, embedded admin HTML/JS in `server.py`

---

### Task 1: P0-02 Sensitive Field Encryption

**Files:**
- Modify: `server.py`
- Test: `tests/security_regression_tests.py`
- Record: `docs/plans/2026-07-10-s2-hard-acceptance-implementation-record.md`

**Step 1: Write the failing tests**

- Add a regression test that saves an account and verifies `accounts.password`, `accounts.ouid`, and `accounts.ouss` are no longer stored as plaintext.
- Add a migration test that inserts legacy plaintext values, runs the migration path, and verifies the stored values are rewritten as ciphertext while runtime session restoration still works.
- Add a backup inspection test that downloads `/api/admin/backup`, opens `accounts.db` from the zip, and verifies plaintext secrets do not appear in the backup database bytes.

**Step 2: Run the tests to verify they fail**

Run: `python -m unittest tests.security_regression_tests.SecurityRegressionTests.test_account_sensitive_fields_are_encrypted_at_rest tests.security_regression_tests.SecurityRegressionTests.test_plaintext_account_secrets_are_migrated_when_encryption_is_available tests.security_regression_tests.SecurityRegressionTests.test_admin_backup_database_does_not_contain_plaintext_account_secrets`

Expected: failures showing plaintext values still exist in `accounts` and in backup contents.

**Step 3: Write minimal implementation**

- Add secret encryption helpers and key resolution.
- Encrypt account password / `OUID` / `ouss` on save.
- Decrypt or tolerate encrypted values on runtime reads.
- Migrate existing plaintext rows when encryption is enabled.

**Step 4: Run the tests to verify they pass**

Run the same targeted test command and confirm all selected `P0-02` tests pass.

### Task 2: P3-02 API Key Fine-Grained Scope

**Files:**
- Modify: `server.py`
- Test: `tests/gateway_hardening_tests.py`
- Record: `docs/plans/2026-07-10-s2-hard-acceptance-implementation-record.md`

**Step 1: Write the failing tests**

- Add schema coverage for `api_keys` scope columns.
- Add admin API tests proving scopes can be saved through `/api/admin/apikeys` and `/api/admin/apikeys/{id}`.
- Add gateway rejection tests for forbidden `kind`, `model_name`, `scene_id`, upload usage, `resolution`, `duration`, and experimental scene access.

**Step 2: Run the tests to verify they fail**

Run: `python -m unittest tests.gateway_hardening_tests.GatewayHardeningTests.test_api_key_scope_columns_exist tests.gateway_hardening_tests.GatewayHardeningTests.test_admin_can_update_api_key_scope_policy tests.gateway_hardening_tests.GatewayHardeningTests.test_generate_rejects_request_outside_api_key_scope tests.gateway_hardening_tests.GatewayHardeningTests.test_upload_rejects_when_api_key_disallows_uploads`

Expected: failures because scope columns, persistence, and enforcement do not exist yet.

**Step 3: Write minimal implementation**

- Extend `api_keys` schema with scope columns.
- Persist scope settings through admin create/update routes and expose them in admin responses.
- Enforce scope limits in `/v1/generate`, `/v1/uploads`, and capability filtering if needed for consistent caller behavior.

**Step 4: Run the tests to verify they pass**

Run the same targeted test command and confirm all selected `P3-02` tests pass.

### Task 3: P5-04 Cost Report

**Files:**
- Modify: `server.py`
- Test: `tests/gateway_hardening_tests.py`
- Record: `docs/plans/2026-07-10-s2-hard-acceptance-implementation-record.md`

**Step 1: Write the failing tests**

- Add an admin API test for a new cost report endpoint that aggregates by date, client, API key, account, model, and task status.
- Cover success cost, failed-but-charged cost, estimated cost, actual cost, and request counts.
- Add a minimal admin HTML/JS regression assertion for the report UI hook.

**Step 2: Run the tests to verify they fail**

Run: `python -m unittest tests.gateway_hardening_tests.GatewayHardeningTests.test_admin_cost_report_aggregates_by_customer_key_account_model_and_status`

Expected: failure because the endpoint/report UI does not exist yet.

**Step 3: Write minimal implementation**

- Add a cost report query endpoint with optional filters.
- Aggregate `usage_log` joined with `api_keys`, `clients`, and `accounts`.
- Add a minimal admin table to query and display the report.

**Step 4: Run the tests to verify they pass**

Run the same targeted test command and confirm the `P5-04` report test passes.

### Task 4: Acceptance Record, Validation, and Leak Check

**Files:**
- Modify: `docs/plans/2026-07-10-image-video-gateway-production-acceptance-checklist.md`
- Create: `docs/plans/2026-07-10-s2-hard-acceptance-implementation-record.md`

**Step 1: Update the implementation record**

- Record audit conclusion for current state (`S1`, not `S2`).
- Record selected IDs, evidence, commands, and residual gaps.

**Step 2: Run full validation**

Run:

```bash
python -m unittest discover -s tests -p "*_tests.py"
python -m py_compile server.py banti_token_generator.py
node -e "const fs=require('fs'); const text=fs.readFileSync('server.py','utf8'); const html=text.match(/ADMIN_HTML = \"\"\"([\\s\\S]*?)\"\"\"/)[1]; const script=html.match(/<script>([\\s\\S]*?)<\\/script>/)[1]; new Function(script); console.log('js parse ok');"
git diff --check
```

**Step 3: Run leak checks**

- Inspect `git diff --stat` / `git diff --check` results.
- Search the diff for accidental secrets before finishing.
