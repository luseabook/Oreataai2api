# S2 Hard Acceptance Implementation Record

## 1. Audit Baseline

### 1.1 Current State Judgment

- Strict acceptance state: `S1`
- Plain-language state: closer to `S1.5`, but still not `S2` because hard blockers remain open.

### 1.2 Current Non-Green IDs Before This Round

| Priority | ID | Baseline before changes | Reason |
|---|---|---|---|
| P0 | P0-01 | 已基本完成 | 需要持续脱敏回归和固定扫描证据 |
| P0 | P0-02 | 未完成 | `accounts.password` / `ouid` / `ouss` 明文存库，备份直接带出原始 DB |
| P0 | P0-03 | 已基本完成 | 需继续依赖失败扣点证据闭环 |
| P0 | P0-05 | 已基本完成 | 会话安全已实现主体，但仍需持续回归证据 |
| P1 | P1-01 | 已基本完成 | 能力发现已存在，但仍依赖回归证据 |
| P1 | P1-05 | 已基本完成 | 异步状态机已有骨架，仍需持续证据 |
| P1 | P1-07 | 已部分完成 | 超时/退避存在，但验收清单仍未转绿 |
| P2 | P2-02 | 未完成 | `reference` 缺真实成功验证 |
| P2 | P2-03 | 未完成 | `frame_based` 缺真实成功验证 |
| P2 | P2-04 | 未完成 | `motion` 缺真实成功验证 |
| P2 | P2-05 | 未完成 | 缺视频实验室 |
| P2 | P2-06 | 未完成 | 缺验证样本库 |
| P3 | P3-01 | 已有骨架 | 客户归属有骨架，但证据不足以转绿 |
| P3 | P3-02 | 未完成 | 无 `kind/model/scene/upload/resolution/duration` 细粒度范围限制 |
| P3 | P3-03 | 未完成 | 缺过期、轮换、来源链路 |
| P3 | P3-04 | 已基本完成 | 配额已有，但仍需持续证据 |
| P3 | P3-05 | 需复核 | 当前仅看得到摘要行为，缺专门验收回归 |
| P5 | P5-01 | 未完成 | 后台任务列表无分页/多维筛选 |
| P5 | P5-02 | 已部分完成 | 详情有基础字段，但未完全达标 |
| P5 | P5-03 | 未完成 | 用量列表无分页/多维筛选 |
| P5 | P5-04 | 未完成 | 无按客户/Key/账号/模型/日期/成功失败扣点聚合报表 |
| P5 | P5-05 | 未完成 | 缺上传素材后台管理 |
| P5 | P5-06 | 已部分完成 | 模型/场景策略后台 patch 已有基础，但仍未完全验收 |

## 2. Selected IDs In 2026-07-10 Round

| ID | Why selected | TDD / validation entry |
|---|---|---|
| P0-02 | 一票否决项，直接阻断 `S2` | 已用失败测试 + 迁移测试 + 备份抽检回归关闭 |
| P3-02 | 一票否决项，直接阻断对外收费开放 | 已用权限拒绝测试和后台策略保存测试关闭 |
| P5-04 | 直接决定是否能按客户/Key/账号/模型对账收费 | 已用聚合报表测试和后台 UI 钩子回归关闭 |

## 3. Commands To Run

```bash
python -m unittest discover -s tests -p "*_tests.py"
python -m py_compile server.py banti_token_generator.py
node -e "const fs=require('fs'); const text=fs.readFileSync('server.py','utf8'); const html=text.match(/ADMIN_HTML = \"\"\"([\\s\\S]*?)\"\"\"/)[1]; const script=html.match(/<script>([\\s\\S]*?)<\\/script>/)[1]; new Function(script); console.log('js parse ok');"
git diff --check
```

## 4. Evidence To Fill After Implementation

### P0-02

- Tests:
  - `tests.security_regression_tests.SecurityRegressionTests.test_account_sensitive_fields_are_encrypted_at_rest`
  - `tests.security_regression_tests.SecurityRegressionTests.test_plaintext_account_secrets_are_migrated_when_encryption_is_available`
  - `tests.security_regression_tests.SecurityRegressionTests.test_admin_backup_database_does_not_contain_plaintext_account_secrets`
- Backup inspection:
  - `/api/admin/backup` 导出的 `accounts.db` 字节流不再包含 `plain-password` / `backup-ouid` / `backup-ouss` 明文。
- Migration evidence:
  - `init_db()` 会在检测到加密 key 时把历史明文 `password` / `ouid` / `ouss` 重写为密文，`CLIENT.session_from_account()` 仍能正确恢复 cookie。

### P3-02

- Tests:
  - `tests.gateway_hardening_tests.GatewayHardeningTests.test_api_key_scope_columns_exist`
  - `tests.gateway_hardening_tests.GatewayHardeningTests.test_admin_can_update_api_key_scope_policy`
  - `tests.gateway_hardening_tests.GatewayHardeningTests.test_generate_rejects_request_outside_api_key_scope`
  - `tests.gateway_hardening_tests.GatewayHardeningTests.test_upload_rejects_when_api_key_disallows_uploads`
- Admin API evidence:
  - `/api/admin/apikeys` 与 `/api/admin/apikeys/{id}` 已支持 `allowed_kinds` / `allowed_models` / `allowed_scenes` / `allowed_resolutions` / `allowed_durations` / `allow_uploads` / `allow_experimental`。
- Rejection evidence:
  - 越权 `kind` / `model` / `scene` / `resolution` / `duration` 请求返回 `403`，上传禁用时 `/v1/uploads` 返回 `API_KEY_UPLOAD_FORBIDDEN`。

### P5-04

- Tests:
  - `tests.gateway_hardening_tests.GatewayHardeningTests.test_admin_cost_report_aggregates_by_customer_key_account_model_and_status`
  - `tests.gateway_hardening_tests.GatewayHardeningTests.test_admin_html_contains_api_key_policy_and_audit_controls`
- API evidence:
  - 新增 `/api/admin/cost-report`，按日期、客户、Key、账号、模型聚合，并输出 `request_count` / `estimated_point_cost` / `actual_point_cost` / `success_actual_point_cost` / `failed_actual_point_cost`。
- Admin UI evidence:
  - 后台 `API Keys` 页已新增“成本报表”表格和 `loadCostReport()` 前端加载逻辑。

## 5. Validation Results

- `python -m unittest discover -s tests -p "*_tests.py"`: `Ran 105 tests in 16.781s`, `OK`
- `python -m py_compile server.py banti_token_generator.py`: passed
- `node ... new Function(script)`: `js parse ok`
- `git diff --check`: passed, only CRLF warnings
- Sensitive leak scan:
  - 对 `git diff` 运行高风险模式扫描后，没有发现长 Bearer token、JWT、云厂商 AK/SK 或 `sk-` 类实密钥。
  - 扫描命中只有测试里的短占位 Bearer 值；未发现真实账号、cookie、API Key、token。

## 6. Residual Gaps After 2026-07-10 Round

- `P2-02` / `P2-03` / `P2-04`: still blocked on real verification, no quota spend without manual approval.
- `P5-01` / `P5-03`: task and usage pagination/filtering remained open after the 2026-07-10 round; closed in section 7.
- `P6-04` / `P6-05`: runbook and deployment docs still needed after the 2026-07-10 round; closed in section 7.
- Final strict state after this round: `S1`

## 7. 2026-07-11 Production Readiness Closure

### 7.1 Selected IDs

| ID | Result | Evidence |
|---|---|---|
| P3-03 | 已完成 | `tests.gateway_hardening_tests.GatewayHardeningTests.test_api_key_lifecycle_columns_exist` and `test_expired_api_key_is_rejected` |
| P3-05 | 已完成 | `tests.gateway_hardening_tests.GatewayHardeningTests.test_api_key_plaintext_is_only_returned_on_create` |
| P5-01 | 已完成 | `tests.gateway_hardening_tests.GatewayHardeningTests.test_admin_tasks_support_full_operational_filters` |
| P5-03 | 已完成 | `tests.gateway_hardening_tests.GatewayHardeningTests.test_admin_usage_supports_full_operational_filters` |
| P5-05 | 已完成 | `tests.gateway_hardening_tests.GatewayHardeningTests.test_admin_uploads_support_listing_filters_and_sanitized_attachments` and admin JS parse |
| P6-04 / P6-05 / P6-06 | 已完成 | `tests.dependency_manifest_tests.DependencyManifestTests.test_runbooks_document_deployment_backup_and_release_checks` |

### 7.2 Implementation Notes

- API Key lifecycle fields are present in initial DDL and compatibility migration: `expires_at`, `rotated_from_id`, `rotation_note`, and `disabled_reason`.
- Expired, disabled, and deleted keys are rejected before gateway execution. OpenAI-compatible routes receive OpenAI-shaped auth errors through the shared exception path.
- Admin task and usage listings now use allowlisted filters and joined operator labels for account email, API key name, and client name.
- Uploaded media now has an admin listing endpoint backed by `uploaded_media`, with account/API Key/client labels, derived media kind, related task count, and recursive removal of upload session keys/cookies from returned attachment metadata.
- Runbooks now cover single application worker deployment, reverse proxy/TLS acknowledgement, `OREATE_ENCRYPTION_KEY`, Node.js / `banti_jt_helper.js`, backup restore, restore verification, stale admin sessions, release commands, and sensitive diff scanning.

### 7.3 Verification Commands For This Closure

```bash
python -m unittest tests.gateway_hardening_tests.GatewayHardeningTests.test_api_key_lifecycle_columns_exist tests.gateway_hardening_tests.GatewayHardeningTests.test_expired_api_key_is_rejected tests.gateway_hardening_tests.GatewayHardeningTests.test_api_key_plaintext_is_only_returned_on_create -v
python -m unittest tests.gateway_hardening_tests.GatewayHardeningTests.test_admin_tasks_support_full_operational_filters tests.gateway_hardening_tests.GatewayHardeningTests.test_admin_usage_supports_full_operational_filters -v
python -m unittest tests.gateway_hardening_tests.GatewayHardeningTests.test_admin_uploads_support_listing_filters_and_sanitized_attachments -v
python -m unittest tests.dependency_manifest_tests -v
node -e 'const fs=require("fs"); const text=fs.readFileSync("server.py","utf8"); const html=text.match(/ADMIN_HTML = """([\s\S]*?)"""/)[1]; const script=html.match(/<script>([\s\S]*?)<\/script>/)[1]; new Function(script); console.log("js parse ok");'
```

### 7.4 Residual Gaps After This Closure

- `P2-04`: `motion` still requires a successful real-credit validation sample before it can be opened beyond experimental/admin validation.
- `P2-05` / `P2-06`: video validation lab and durable sample library remain open.
- `P4-04`: automatic account maintenance remains open.
- Current strict state: S2-ready for already verified capabilities, including `reference` and `frame_based`; not full S2 for the `motion` advanced video scene.

### 7.5 Final Verification Results

- `python -m unittest discover -s tests -p "*_tests.py" -v`: `Ran 192 tests in 14.950s`, `OK`
- `python -m py_compile server.py banti_token_generator.py`: passed
- `node ... new Function(script)`: `js parse ok`
- `git diff --check`: passed, only CRLF warnings from the existing Windows worktree
- Sensitive diff scan: no matches for AWS-style keys, long `sk-` keys, long Bearer tokens, JWTs, or Oreate session cookie assignments

## 8. 2026-07-12 Advanced Video Live Validation

### 8.1 Successful Samples

| ID | Result | Evidence |
|---|---|---|
| P2-02 `reference` | 已完成 | `live_validation/advanced-video-validation-20260711-235634.json`, task `7`, `Seedance 2.0 Mini`, MP4 `https://cdn.oreateai.com/aivideo/videodownload/2698213035.mp4`, preview `live_validation/advanced-video-validation-20260711-235634/reference-1-preview.jpg` |
| P2-03 `frame_based` | 已完成 | `live_validation/advanced-video-validation-20260712-001820-retry.json`, task `10`, `Seedance 1.5 Pro`, MP4 `https://cdn.oreateai.com/aivideo/videodownload/2543040596.mp4`, preview `live_validation/advanced-video-validation-20260712-001820/frame_based_seedance_15_pro-1-preview.jpg` |

### 8.2 Motion Attempts

- `live_validation/advanced-video-validation-20260712-000324.json`: task `9`, `Kling 2.6`, uploaded local MP4, failed with `100003 call service error`.
- `live_validation/advanced-video-validation-20260712-001820-retry.json`: task `11`, generated reference MP4 as motion source, failed with `100004 internal error`.
- `live_validation/advanced-video-validation-20260712-002744.json`: task `12`, local MP4 after video metadata enrichment, failed with `100003 call service error`.
- `live_validation/advanced-video-validation-20260712-003127.json`: task `13`, generated full-body character and 3-second 512x512 motion video, failed with `100003 call service error`.
- `live_validation/advanced-video-validation-20260712-003426.json`: task `14`, motion config aligned with current web restrictions by omitting generated `duration` and clearing `ratio`, failed with `100003 call service error`.
- `live_validation/advanced-video-validation-20260712-003622.json`: task `15`, `Kling 3.0` with `keepOriginalSound=true`, failed with `200017 point exceed`.

### 8.3 Implementation Corrections From Live Validation

- Video uploads now skip `/oreate/convert/submit` after BOS upload; image uploads still call convert submit and preserve `docId` / `parseInfo`.
- MP4/MOV uploads now enrich attachments with `videoDurationSec`, `videoWidth`, and `videoHeight` when container metadata is readable.
- Motion video config now follows current web restrictions for `motion`: no generated `duration`, empty `ratio`, `isAudio=false`, and motion cost selection by `motDuration`.
- `live_validation/run_advanced_video_live_validation.py` supports env overrides for first/last frame, character image, motion video, motion model, motion duration, resolution, and keep-original-sound, while keeping advanced scene policy enabled only in-process.

### 8.4 Current Blocker

Realtime balance probes show accounts `2` through `25` all have `daily_point=0`, `bonus_point=0`, and `rest_point=0`. Programmatic new-account registration remains blocked by mailbox verification flow. `P2-04 motion` therefore remains not green and must stay disabled for ordinary external API keys until a real successful sample exists.

Guardrail: Do not register or rotate into new upstream accounts to bypass HTTP 403, quota exhaustion, or risk controls. Only rerun `P2-04 motion` with a legitimately provisioned upstream account that has sufficient points, and keep `P2-04` incomplete until the evidence file contains a completed task with a reachable MP4 asset. A 2026-07-12 follow-up balance probe reconfirmed accounts `2` through `25` at `rest_point=0`, `daily_point=0`, and `bonus_point=0`, so no `motion` generation was started.

### 8.5 Verification Results After This Update

- `python -m unittest discover -s tests -p "*_tests.py" -v`: `Ran 196 tests in 15.393s`, `OK`.
- `python -m py_compile server.py banti_token_generator.py live_validation/run_advanced_video_live_validation.py`: passed.
- `node ... new Function(script)`: `js parse ok`.
- `git diff --check`: passed, only CRLF warnings from the existing Windows worktree.
- Sensitive diff scan: no matches for AWS-style keys, long `sk-` keys, long Bearer tokens, JWTs, or Oreate session cookie assignments.
