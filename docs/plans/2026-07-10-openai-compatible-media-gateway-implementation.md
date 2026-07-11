# OpenAI-Compatible Media Gateway Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn the existing Oreate adapter into a production-ready image/video gateway with OpenAI-compatible image and video APIs.

**Architecture:** Keep the existing native task API and SQLite task records as the execution core. Add a pure compatibility module for OpenAI request/response mapping, wire resource-oriented routes in FastAPI, then close security, consistency, recovery, and deployment blockers with test-first changes.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic 2, SQLite, requests, cryptography/Fernet, Node.js Banti helper, unittest/TestClient.

---

### Task 1: Reproducible test environment and dependency manifest

**Files:**
- Modify: `requirements.txt`
- Create: `requirements-dev.txt`
- Modify: `README.md`

1. Add a smoke test that imports `server` in a clean environment and confirms multipart routes register.
2. Run it before changing dependencies and record the missing dependency failure.
3. Add `python-multipart` to runtime requirements and `httpx` to development requirements; document Python and Node minimum versions.
4. Create an isolated environment under `E:\tmp`, install manifests, and run the smoke test.

### Task 2: Pure OpenAI compatibility primitives

**Files:**
- Create: `gateway/__init__.py`
- Create: `gateway/openai_compat.py`
- Create: `tests/openai_compat_tests.py`

1. Write failing tests for reversible video IDs, task status/progress mapping, model aliases, image size mapping, video size mapping, and OpenAI error envelopes.
2. Run the focused tests and confirm failures are caused by missing implementation.
3. Implement only pure mapping/validation functions.
4. Run focused tests and the existing suite.

### Task 3: OpenAI-compatible model discovery

**Files:**
- Modify: `server.py`
- Modify: `tests/openai_compat_tests.py`

1. Write failing tests for authenticated `GET /v1/models`, OpenAI list shape, alias records, and API-key scope filtering.
2. Verify the current endpoint is absent and `/v1/capabilities` currently ignores key scope.
3. Implement policy-filtered capability loading and model list responses without account IDs.
4. Run focused and security tests.

### Task 4: OpenAI-compatible image generation

**Files:**
- Modify: `server.py`
- Modify: `gateway/openai_compat.py`
- Modify: `tests/openai_compat_tests.py`

1. Write failing tests for `POST /v1/images/generations`: auth, request mapping, `n=1`, URL-only response, bounded timeout, completed response, and OpenAI error shape.
2. Implement a shared internal task-submission function so native and compatibility routes use identical policy/quota/idempotency behavior.
3. Return only stable OpenAI fields and never raw task/provider/account data.
4. Run focused and full tests.

### Task 5: OpenAI-compatible video jobs

**Files:**
- Modify: `server.py`
- Modify: `gateway/openai_compat.py`
- Modify: `tests/openai_compat_tests.py`

1. Write failing tests for create, generation alias, retrieve, completed content, non-completed content conflict, ownership, and delete/cancel terminal-state rules.
2. Implement `POST /v1/videos`, `POST /v1/videos/generations`, `GET /v1/videos/{id}`, `GET /v1/videos/{id}/content`, and `DELETE /v1/videos/{id}`.
3. Restrict content URLs to HTTPS and an explicit CDN hostname allowlist.
4. Run focused and full tests.

### Task 6: Stable validation and error handling

**Files:**
- Modify: `server.py`
- Modify: `tests/openai_compat_tests.py`

1. Write failing tests for Pydantic validation errors, invalid model/size/duration, quota errors, and upstream errors on compatibility routes.
2. Add a `RequestValidationError` handler that selects OpenAI or native envelopes by route family.
3. Bound prompt length, idempotency key length, request ID length, and client-controlled sync wait.
4. Run all API contract tests.

### Task 7: P0 administrator and secret safety

**Files:**
- Modify: `server.py`
- Modify: `tests/security_regression_tests.py`
- Modify: `config.example.json`
- Modify: `README.md`

1. Write a failing stored-XSS regression test for usage prompts and account/client fields.
2. Escape every user/upstream-controlled value inserted into administrator `innerHTML`; add a restrictive CSP response header.
3. Write readiness tests requiring an encryption key when account secrets exist and rejecting placeholder administrator credentials.
4. Document key generation, backup separation, migration, rotation, and recovery; never print the key.

### Task 8: Upload and request abuse boundaries

**Files:**
- Modify: `server.py`
- Modify: `tests/gateway_hardening_tests.py`
- Modify: `config.example.json`

1. Write failing tests for maximum upload bytes, allowed media types/extensions, streamed reading, upload rate limiting, and sanitized upstream errors.
2. Add configured limits and chunked reads; reject before forwarding when possible.
3. Apply API-key rate accounting to uploads and task actions.
4. Run upload, quota, and security tests.

### Task 9: Atomic idempotency and retry accounting

**Files:**
- Modify: `server.py`
- Modify: `tests/gateway_hardening_tests.py`

1. Write concurrent tests proving one task is created for a shared idempotency key and quotas cannot be oversubscribed.
2. Reserve idempotency keys transactionally before task creation and persist the task/result association atomically.
3. Enforce TTL cleanup and route/request hashing; update replay state from the current task.
4. Re-check scope, quota, balance, and account selection on retries; record each billable attempt instead of overwriting cost.

### Task 10: Durable worker recovery and scheduling

**Files:**
- Modify: `server.py`
- Modify: `tests/gateway_hardening_tests.py`

1. Write failing tests for stale-running recovery, graceful shutdown, worker liveness readiness, queue bursts across accounts, and retry failover.
2. Add task leases and startup recovery; clear leases on terminal transitions.
3. Select/reserve accounts at execution time and enforce per-account concurrency.
4. Add bounded automatic retries only for classified transient failures.

### Task 11: Schema, indexing, operations, and CI

**Files:**
- Create: `migrations/` versioned migration scripts
- Create: `.github/workflows/test.yml`
- Create: `docs/runbooks/gateway-deployment.md`
- Create: `docs/runbooks/backup-restore.md`
- Modify: `server.py`

1. Add migration tests for the repository’s current legacy database shape.
2. Add indexes for task claims, tenant task lookup, usage quota windows, idempotency lookup, and account scheduling; enable foreign keys and WAL with a bounded busy timeout.
3. Add deployment, upgrade, secret, Node/Banti, backup/restore, and rollback runbooks.
4. Run CI-equivalent syntax, unit, security, migration, and contract commands in a clean environment.

### Task 12: Production acceptance

**Files:**
- Modify: `docs/plans/2026-07-10-image-video-gateway-production-acceptance-checklist.md`
- Create: `live_validation/README.md`

1. Run all automated tests and record exact counts and environment versions.
2. Run concurrency and bounded load tests without real generation spend.
3. With explicit human approval, perform lowest-cost real image, text-video, and every enabled upload-video scenario; record balance deltas and CDN checks without credentials.
4. Verify a clean restore, stale-session revocation, worker restart recovery, and key rotation.
5. Mark S2 only when every mandatory acceptance item has current evidence.
