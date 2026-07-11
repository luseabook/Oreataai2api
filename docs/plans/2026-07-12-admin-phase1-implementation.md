# Admin Phase 1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the admin console's configuration, API-key limit, task-state, and paginated-list correctness gaps without changing its deployment shape.

**Architecture:** Keep the embedded admin page for this phase, strengthen FastAPI/Pydantic contracts, add server-enforced task state guards and list totals, then wire the existing vanilla UI to those contracts through small reusable helpers. Preserve all existing routes and response fields.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic 2, SQLite, vanilla HTML/CSS/JavaScript, unittest, Node.js syntax validation.

---

### Task 1: Validate admin settings and communicate restart requirements

**Files:**
- Modify: `server.py:2490-2494`
- Modify: `server.py:7054-7060`
- Modify: `server.py:7560-7579`
- Modify: `server.py:7731-7738`
- Modify: `server.py:8197-8219`
- Test: `tests/security_regression_tests.py`

**Step 1: Write failing HTTP tests**

Add tests proving:

- ports outside `1..65535` and non-integer ports return `422` without changing `CFG` or `config.json`;
- negative pool values return `422`;
- a merged configuration with `maintain_target < min_accounts` returns `422` without persistence;
- changing the port returns `restart_required: true`, while changing only pool settings returns `false`.

**Step 2: Run the focused tests and verify RED**

Run:

```powershell
E:\oreateai\.venv\Scripts\python.exe -m unittest tests.security_regression_tests.SecurityRegressionTests.test_admin_settings_reject_invalid_numeric_values tests.security_regression_tests.SecurityRegressionTests.test_admin_settings_reject_inconsistent_pool_targets tests.security_regression_tests.SecurityRegressionTests.test_admin_settings_reports_restart_requirement -v
```

Expected: failures because validation and `restart_required` do not exist.

**Step 3: Implement the minimal backend contract**

- Add nested Pydantic input models for known server/pool fields with `extra="allow"`.
- Validate the merged pool relationship before assigning global `CFG`.
- Compute `restart_required` before mutation.
- Do not write the config on validation failure.

**Step 4: Add frontend numeric validation and readable API errors**

- Change number inputs to semantic numeric controls.
- Add a shared required-integer parser.
- Convert Pydantic detail arrays into readable messages.
- Show a restart-required success message when appropriate.

**Step 5: Run focused tests and JS parse check**

```powershell
E:\oreateai\.venv\Scripts\python.exe -m unittest tests.security_regression_tests -v
node -e "const fs=require('fs');const t=fs.readFileSync('server.py','utf8');const h=t.match(/ADMIN_HTML = \"\"\"([\\s\\S]*?)\"\"\"/)[1];new Function(h.match(/<script>([\\s\\S]*?)<\\/script>/)[1]);"
```

### Task 2: Preserve API Key limit semantics and normalized status

**Files:**
- Modify: `server.py:7513-7517`
- Modify: `server.py:8060-8064`
- Modify: `server.py:8111-8127`
- Test: `tests/gateway_hardening_tests.py`

**Step 1: Write failing frontend contract tests**

Add assertions proving the embedded UI:

- renders numeric zero with nullish semantics rather than `||`;
- parses blank as `null`, `0` as zero, positive integers unchanged, and rejects negatives/non-integers;
- uses `k.status` so expired/deleted keys are not displayed as merely disabled;
- labels empty and zero semantics in the table header or input help.

**Step 2: Run focused test and verify RED**

```powershell
E:\oreateai\.venv\Scripts\python.exe -m unittest tests.gateway_hardening_tests.GatewayHardeningTests.test_admin_html_preserves_api_key_limit_semantics -v
```

**Step 3: Implement minimal JavaScript helpers**

- Add a pure optional non-negative integer parser.
- Replace `value || null` and `k.limit || ''` patterns.
- Render normalized backend status with stable tag classes.
- Keep blank-as-inherit behavior backward compatible.

**Step 4: Run the focused test and JS parse check**

Run the focused unittest and the Node syntax command from Task 1.

### Task 3: Enforce task action state boundaries

**Files:**
- Modify: `server.py:3746-3752`
- Modify: `server.py:6837-6863`
- Modify: `server.py:7962-7984`
- Modify: `server.py:8041-8054`
- Test: `tests/gateway_hardening_tests.py`

**Step 1: Write failing backend tests**

Add tests proving:

- completed, failed, and expired tasks return `409` from cancel and retain their original state;
- queued tasks can be cancelled;
- cancelled tasks remain idempotent;
- the admin HTML exposes retry/hydrate/cancel only through status predicates and confirms cancellation.

**Step 2: Run focused tests and verify RED**

```powershell
E:\oreateai\.venv\Scripts\python.exe -m unittest tests.gateway_hardening_tests.GatewayHardeningTests.test_terminal_tasks_cannot_be_cancelled tests.gateway_hardening_tests.GatewayHardeningTests.test_admin_html_gates_task_actions_by_status -v
```

**Step 3: Implement server guard and UI predicates**

- Add `task_cancellable_status`.
- Raise `GatewayAPIError(409, "TASK_NOT_CANCELLABLE", ...)` before mutation.
- Keep already-cancelled requests idempotent.
- Render only legal actions and add a task-specific confirmation for cancel.

**Step 4: Run focused tests and adjacent task tests**

```powershell
E:\oreateai\.venv\Scripts\python.exe -m unittest tests.gateway_hardening_tests.GatewayHardeningTests.test_terminal_tasks_cannot_be_cancelled tests.gateway_hardening_tests.GatewayHardeningTests.test_task_retry_cancel_and_hydrate_actions_work tests.openai_compat_tests.OpenAICompatEndpointTests.test_video_delete_cancels_active_job_but_preserves_completed_job -v
```

### Task 4: Wire task, usage, and upload filters and pagination

**Files:**
- Modify: `server.py:5513-5587`
- Modify: `server.py:5602-5668`
- Modify: `server.py:7161-7239`
- Modify: `server.py:7460-7474`
- Modify: `server.py:7526-7540`
- Modify: `server.py:7730-7739`
- Modify: `server.py:7960-7984`
- Modify: `server.py:8130-8148`
- Modify: `server.py:8242-8247`
- Test: `tests/gateway_hardening_tests.py`

**Step 1: Write failing API tests for totals**

Extend existing list tests to require `total` to reflect all matching rows independently of page size and offset.

**Step 2: Run focused API tests and verify RED**

```powershell
E:\oreateai\.venv\Scripts\python.exe -m unittest tests.gateway_hardening_tests.GatewayHardeningTests.test_admin_tasks_support_limit_offset_and_status_filter tests.gateway_hardening_tests.GatewayHardeningTests.test_admin_usage_supports_limit_offset_and_filters tests.gateway_hardening_tests.GatewayHardeningTests.test_admin_uploads_support_listing_filters_and_sanitized_attachments -v
```

Expected: failures because `total` is absent.

**Step 3: Add totals without changing existing pagination fields**

Run a count query with the exact same filters before each paginated select. Return `total` alongside `items`, `limit`, `offset`, and `has_more`.

**Step 4: Write failing admin HTML tests**

Require:

- task, usage, and upload filter controls;
- previous/next pagination controls and page status;
- query construction through `URLSearchParams`;
- consumption of `total` and `has_more`;
- absence of `.slice(0,50)` for these resources.

**Step 5: Implement reusable list state and controls**

- Store page state per resource.
- Reset offset when filters change.
- Pass all supported filter values to the APIs.
- Render loading, empty, error, and page-count states separately.
- Update task statistics from `total`, not loaded item count.

**Step 6: Run focused tests and JS parse check**

Run the three API tests, admin HTML tests, and Node syntax check.

### Task 5: Full verification and review

**Files:**
- Review: `server.py`
- Review: `tests/gateway_hardening_tests.py`
- Review: `tests/security_regression_tests.py`
- Review: `docs/plans/2026-07-12-admin-phase1-design.md`

**Step 1: Run the full test suite**

```powershell
E:\oreateai\.venv\Scripts\python.exe -m unittest discover -s tests -p '*_tests.py' -v
```

**Step 2: Parse embedded JavaScript**

```powershell
node -e "const fs=require('fs');const t=fs.readFileSync('server.py','utf8');const h=t.match(/ADMIN_HTML = \"\"\"([\\s\\S]*?)\"\"\"/)[1];new Function(h.match(/<script>([\\s\\S]*?)<\\/script>/)[1]);console.log('admin JS parse ok');"
```

**Step 3: Inspect the final diff**

Confirm no route removals, no secret material, no unrelated refactors, and no changes to the user's untracked scripts in the original workspace.

