# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repo-documented setup and run commands

- Runtime requirements: Python 3.11+ and Node.js 18+. The Python service calls the local Node helper `banti_jt_helper.js` during generation traffic; there is no checked-in `package.json` or separate frontend build.
- Install runtime dependencies:
  ```bash
  pip install -r requirements.txt
  ```
- Install test-only dependencies:
  ```bash
  pip install -r requirements-dev.txt
  ```
- First-time local config:
  ```bash
  cp config.example.json config.json
  ```
- Generate an encryption key for account-secret storage:
  ```bash
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
- Export the key before starting the service:
  ```bash
  export OREATE_ENCRYPTION_KEY="<generated key>"
  ```
- Run the service:
  ```bash
  python server.py
  ```
- Alternative app launch:
  ```bash
  uvicorn server:app --workers 1
  ```
- External process-manager reminder: any declared worker-count variables must agree on `1`. Common declarations the runtime validates are `OREATE_APP_WORKERS=1`, `WEB_CONCURRENCY=1`, and `GUNICORN_CMD_ARGS="--workers 1"`. If the service manager needs a separate runtime directory for the lock file, set `OREATE_WORKER_LOCK_PATH`.
- Useful local readiness checks after startup:
  ```bash
  curl http://127.0.0.1:8890/healthz
  curl http://127.0.0.1:8890/readyz
  ```

## Verified test commands

- Run the full repository test suite:
  ```bash
  python -m unittest discover -s tests -p '*_tests.py' -v
  ```
  Note: default `unittest discover` without `-p '*_tests.py'` finds 0 tests because the repo uses `*_tests.py` filenames.
- Run one test module:
  ```bash
  python -m unittest tests.openai_compat_tests -v
  ```
- Run one test case:
  ```bash
  python -m unittest tests.openai_compat_tests.OpenAICompatPrimitiveTests -v
  ```
- Run one specific test method:
  ```bash
  python -m unittest tests.openai_compat_tests.OpenAICompatPrimitiveTests.test_video_ids_are_reversible_and_reject_invalid_values -v
  ```

There is no separate lint or build command checked into this repo; the main validation path is the `unittest` suite plus running the FastAPI service. When changing runtime/test dependencies or documented setup prerequisites, update `requirements.txt`, `requirements-dev.txt`, `README.md`, and `tests/dependency_manifest_tests.py` together.

## Non-obvious operational constraints

- This gateway must run as exactly one application worker. `server.py` starts Uvicorn with `workers=1`, and `gateway/runtime.py` enforces single-worker declarations plus an OS lock file because rate limiting, queue scheduling, and in-flight task state are process-local.
- Public bind is intentionally blocked unless all three deployment acknowledgements are enabled in config: `deployment.allow_public_bind`, `deployment.trust_reverse_proxy`, and `deployment.tls_terminated_by_proxy`. `/readyz` treats missing acknowledgement as not-ready.
- `OREATE_ENCRYPTION_KEY` is not optional once account secrets exist. Readiness checks and secret-migration paths assume it is available.
- OpenAI-compatible asset serving trusts configured CDN hosts via `openai_compat.asset_host_allowlist`, while the admin page also hardcodes CSP allowances for `https://cdn.oreateai.com`. If asset origins change, keep both in sync.

## High-level architecture

### Runtime shape

- `server.py` is the application center of gravity today. It contains:
  - config loading and redaction helpers
  - SQLite schema creation and compatibility migrations
  - upstream Oreate and YYDS mail clients
  - FastAPI route definitions for admin, native gateway, and OpenAI-compatible APIs
  - the background task queue / retry / hydration worker
  - the embedded `/admin` HTML + JavaScript UI
- Change routing guideline: OpenAI request/response contract changes belong in `gateway/openai_compat.py`; deployment safety changes belong in `gateway/runtime.py`; route/admin/task behavior currently lives in `server.py`. When adding new pure mapping, validation, or runtime helpers, prefer keeping or extracting them under `gateway/` instead of expanding `server.py` further.
- `gateway/openai_compat.py` is intentionally pure and side-effect-free. It handles OpenAI request/response mapping, model alias resolution, OpenAI-shaped errors, `video_<id>` encoding, and multipart/json reference splitting. Use it for compatibility-contract changes without mixing in DB or HTTP behavior.
- `gateway/runtime.py` isolates deployment safety checks for worker-count validation and the single-worker lock.
- `banti_token_generator.py` is the Python entrypoint for jt token generation, but the preferred live path shells out to `banti_jt_helper.js`. If generation starts failing after upstream protocol changes, inspect that boundary first.

### Data model and persistence

- SQLite is the only persistence layer. Base tables are created in `server.py:init_db()`, while `migrations/001_operational_indexes.sql` adds scheduler and lookup indexes.
- Important table groups:
  - `accounts`: upstream Oreate credentials, cookies, cached model/video capabilities, and point-balance snapshots
  - `tasks` + `task_attempts`: queued work, retry state, hydration state, and per-attempt execution details
  - `api_keys`, `clients`, `usage_log`, `idempotency_keys`: tenant controls, quotas, auditability, and replay protection
  - `uploaded_media`: ownership-tracked upload objects that can be reused in video scenes
  - `admin_sessions`, `admin_audit_log`: admin auth state and audit trail
- `init_db()` both creates tables and incrementally adds columns. Schema work usually has to touch initial DDL, compatibility `ALTER TABLE` logic, and any operational indexes together.

### Request flow

- Native gateway endpoints are the primary execution path:
  - `/v1/generate`
  - `/v1/uploads`
  - `/v1/tasks`, `/v1/tasks/{task_id}`, retry/cancel/hydrate task actions
  - `/v1/capabilities`
- OpenAI-compatible endpoints are wrappers over the same task system, not a separate execution stack:
  - `/v1/models`
  - `/v1/images/generations`
  - `/v1/videos`, `/v1/videos/generations`
  - `/v1/videos/{video_id}` and `/v1/videos/{video_id}/content`
- Capability data is not hard-coded. The gateway caches image/video capabilities on accounts, normalizes them, and then filters them again through per-API-key scopes and policy flags.
- Upload-backed video generation is strict about attachment provenance: callers must use objects returned by `/v1/uploads`; placeholder filenames or local paths are rejected before any upstream call.

### Media execution details that affect refactors

- Both image and video generation follow the Oreate web flow rather than a simplified provider API. The gateway creates a chat session first, then submits generation over `/oreate/sse/stream`.
- Video handling is two-phase by design: upstream SSE may stay at `start/ping` without a final `end`, so the worker often has to hydrate results from Oreate message history after submission. Preserve this queue + hydration split when touching task execution.
- The background worker is optional in config, but the same task state machine is used in both worker-driven and synchronous wait flows.
- Experimental scene policy matters to behavior: default config enables `text_or_image` but keeps `reference`, `frame_based`, and `motion` disabled until explicitly enabled.

### Admin surface

- The admin frontend is not a separate app; it is an inline HTML/JS blob served from `/admin` inside `server.py`. Backend `/api/*` changes often require matching edits in that embedded page.
- Sensitive config redaction is handled in server-side helpers before returning admin settings; config-shape changes should keep the public/redacted views aligned.

## Test map

- `tests/gateway_hardening_tests.py`: queueing, quotas, idempotency, upload validation, scheduler behavior, capability policy handling, and operational metrics/readiness.
- `tests/openai_compat_tests.py`: pure OpenAI mapping helpers plus OpenAI-style endpoint behavior.
- `tests/runtime_safety_tests.py`: single-worker enforcement, lock lifecycle, startup/shutdown guarantees.
- `tests/security_regression_tests.py`: admin auth/session behavior, encryption-at-rest, backup/restore, redaction, readiness checks, and defensive admin page headers.
- `tests/dependency_manifest_tests.py`: guardrails for required dependency manifests and README-documented runtime prerequisites.
