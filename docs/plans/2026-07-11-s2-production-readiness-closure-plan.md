# S2 Production Readiness Closure Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the remaining automatable S2 blockers for the image/video gateway while keeping real-credit video validation behind explicit human approval.

**Architecture:** Keep the current FastAPI + SQLite + embedded admin UI shape. Extend the existing task, usage, API key, and upload tables rather than introducing a second operations store. Treat live advanced-video validation as evidence capture, not normal request execution.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic 2, SQLite WAL, requests, cryptography/Fernet, Node.js Banti helper, unittest/TestClient.

---

## Scope

### In Scope This Round

- `P5-01`: Admin task pagination and filters for status, kind, model, scene, account, API Key, client, error code, and date range.
- `P5-03`: Admin usage pagination and filters for client, API Key, account, kind, model, status, error code, and date range.
- `P5-05`: Admin uploaded-media listing and attachment copy surface backed by `uploaded_media`.
- `P3-03` / `P3-05`: API Key lifecycle fields for expiration, rotation source, disabled reason, and create-once plaintext display.
- `P6-04` / `P6-05`: Backup/restore and deployment runbooks.
- `P6-06`: A fixed pre-release verification checklist.
- Acceptance-document synchronization after implementation.

### Explicitly Out Of Scope Without Human Approval

- `P2-02`, `P2-03`, `P2-04`: real `reference`, `frame_based`, and `motion` generation runs because they consume upstream credits.
- Opening unverified advanced scenes to ordinary external API keys.

### Prepared But Not Fully Green Without Live Evidence

- `P2-05` / `P2-06`: create a validation-evidence format and admin-visible policy fields where practical, but mark live scene evidence incomplete until approved validation runs exist.

---

## Task 1: Document Current S2 Closure Target

**Files:**
- Create: `docs/plans/2026-07-11-s2-production-readiness-closure-plan.md`
- Modify after implementation: `docs/plans/2026-07-10-image-video-gateway-production-acceptance-checklist.md`

**Step 1: Write this plan before code changes**

Capture in-scope, out-of-scope, verification commands, and the remaining manual approval gate for credit-spending validation.

**Step 2: Keep acceptance status honest**

After implementation, update only the acceptance IDs backed by tests or docs. Leave `P2-02` / `P2-03` / `P2-04` as not green unless real validation samples are produced with approval.

---

## Task 2: API Key Lifecycle And Plaintext Display Rules

**Files:**
- Modify: `server.py`
- Modify: `tests/gateway_hardening_tests.py`
- Modify if needed: `tests/security_regression_tests.py`

**Step 1: Write failing tests**

Add tests proving:

- `api_keys` has `expires_at`, `rotated_from_id`, and `rotation_note` columns.
- Expired keys are rejected by `/v1/generate` and OpenAI-compatible routes.
- Admin create returns full `key` once, while list/update responses only return `key_preview`.
- Admin can disable a key with `disabled_reason` without deleting historical usage.

Run:

```bash
python -m unittest tests.gateway_hardening_tests.GatewayHardeningTests.test_api_key_lifecycle_columns_exist tests.gateway_hardening_tests.GatewayHardeningTests.test_expired_api_key_is_rejected tests.gateway_hardening_tests.GatewayHardeningTests.test_api_key_plaintext_is_only_returned_on_create -v
```

Expected before implementation: failures for missing columns or missing lifecycle enforcement.

**Step 2: Implement minimal lifecycle support**

- Extend initial `api_keys` DDL and compatibility migration.
- Include lifecycle fields in `public_api_key`.
- Reject disabled, deleted, or expired keys in `get_api_key`.
- Allow admin create/update payloads to set `expires_at`, `disabled_reason`, and rotation metadata.
- Keep `reveal=True` only on create.

**Step 3: Verify**

Run the focused tests and then the full suite.

---

## Task 3: Full Admin Task And Usage Filters

**Files:**
- Modify: `server.py`
- Modify: `tests/gateway_hardening_tests.py`

**Step 1: Write failing tests**

Add tests proving:

- `/api/tasks` filters by `model_name`, `scene_id`, `account_id`, `api_key_id`, `client_id`, `error_code`, `date_from`, and `date_to`.
- `/api/admin/usage` filters by `client_id`, `api_key_id`, `account_id`, `kind`, `model_name`, `status`, `error_code`, `date_from`, and `date_to`.
- Responses include enough joined labels for operators: account email, API key name, and client name where applicable.
- Invalid dates and list limits return bounded `422` or `400` errors.

Run:

```bash
python -m unittest tests.gateway_hardening_tests.GatewayHardeningTests.test_admin_tasks_support_full_operational_filters tests.gateway_hardening_tests.GatewayHardeningTests.test_admin_usage_supports_full_operational_filters -v
```

Expected before implementation: failures for unsupported query parameters and missing joins.

**Step 2: Implement helper-based filters**

- Add a small date-boundary parser reusable by task, usage, and cost-report endpoints.
- Build SQL `WHERE` clauses from explicit allowlisted fields.
- Join `api_keys`, `clients`, and `accounts` only for admin endpoints.
- Preserve tenant isolation on `/v1/tasks`.

**Step 3: Verify**

Run focused tests, then all gateway hardening tests.

---

## Task 4: Uploaded Media Admin Management

**Files:**
- Modify: `server.py`
- Modify: `tests/gateway_hardening_tests.py`

**Step 1: Write failing tests**

Add tests proving:

- `/api/admin/uploads` requires admin auth.
- It supports `limit`, `offset`, `api_key_id`, `account_id`, `status`, `kind`, and date filters.
- It returns sanitized attachment metadata, object path, account email, API key name, and related task count.
- It does not expose upstream upload session keys or cookies.

Run:

```bash
python -m unittest tests.gateway_hardening_tests.GatewayHardeningTests.test_admin_uploads_support_listing_filters_and_sanitized_attachments -v
```

Expected before implementation: `404` for missing endpoint.

**Step 2: Implement the endpoint**

- Query `uploaded_media` with joins to `accounts` and `api_keys`.
- Derive media kind from attachment extension or content type.
- Return the stored attachment object because it is already the reusable gateway contract, but redact any sensitive key names defensively.
- Add a small admin HTML table and loader for uploaded media.

**Step 3: Verify**

Run focused test and admin HTML parse.

---

## Task 5: Production Runbooks And Release Checklist

**Files:**
- Create: `docs/runbooks/gateway-deployment.md`
- Create: `docs/runbooks/backup-restore.md`
- Create: `docs/runbooks/release-checklist.md`
- Modify if needed: `README.md`

**Step 1: Write documentation tests first**

Add or extend dependency/documentation tests proving runbooks mention:

- single application worker
- reverse proxy and TLS acknowledgement
- `OREATE_ENCRYPTION_KEY`
- Node.js Banti helper
- backup restore, restore verification, and stale-session revocation
- release commands: unittest, py_compile, admin JS parse, `git diff --check`, sensitive diff scan

Run:

```bash
python -m unittest tests.dependency_manifest_tests -v
```

Expected before implementation: failures for missing runbook files or required text.

**Step 2: Add runbooks**

Write concise operator-facing runbooks with exact commands and rollback checks. Do not include real credentials.

**Step 3: Verify**

Run documentation tests and final verification commands.

---

## Task 6: Acceptance Sync And Final Verification

**Files:**
- Modify: `docs/plans/2026-07-10-image-video-gateway-production-acceptance-checklist.md`
- Modify: `docs/plans/2026-07-10-s2-hard-acceptance-implementation-record.md`

**Step 1: Update status backed by evidence**

Update:

- `P3-03` / `P3-05` if lifecycle and plaintext rules pass tests.
- `P5-01` / `P5-03` if full operational filters pass tests.
- `P5-05` if uploaded-media admin listing passes tests.
- `P6-04` / `P6-05` / `P6-06` if runbooks and release checklist exist and tests pass.

Do not mark `P2-02` / `P2-03` / `P2-04` green without real validation files.

**Step 2: Run final verification**

```bash
python -m unittest discover -s tests -p "*_tests.py" -v
python -m py_compile server.py banti_token_generator.py
node -e 'const fs=require("fs"); const text=fs.readFileSync("server.py","utf8"); const html=text.match(/ADMIN_HTML = """([\s\S]*?)"""/)[1]; const script=html.match(/<script>([\s\S]*?)<\/script>/)[1]; new Function(script); console.log("js parse ok");'
git diff --check
SECRET_SCAN_PATTERN='AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,}|Bearer [A-Za-z0-9._~+/=-]{30,}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}|OUID'"="'|ouss'"="'
git diff -- . | rg -n "$SECRET_SCAN_PATTERN" -S
```

Expected:

- unittest passes
- py_compile passes
- admin JS parse prints `js parse ok`
- `git diff --check` has no whitespace errors
- sensitive diff scan has no real-secret matches

**Step 3: Final state language**

If all automatable work passes but live advanced video evidence is still missing, report the state as:

> S2-ready for already verified capabilities, but not full S2 for advanced video scenes until approved live samples exist.
