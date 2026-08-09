# 号池健康三态 + 隔离（主线 A）

日期：2026-07-24  
原则：ponytail / YAGNI — 不重写选号，不重做 Admin SPA，不引入 Redis。

## 目标

把「展示用 health」升级为**可调度的三态维度**，并抽出纯逻辑模块，避免再在 `server.py` 堆分支。

```text
auth_ready  →  points_ready  →  generate_ready
  登录/会话      余额够门槛        可真正入选生成
```

## 现状（已具备，勿重复造）

| 能力 | 位置 |
|------|------|
| 合成 `health_status` | `server.py` `account_health_status` |
| 冷却 / 余额门槛选号 | `candidate_accounts_for_generation` + `select_generation_account` |
| failover 冷却 / invalid | `mark_account_failure` |
| Admin 标签展示 | `gateway/admin_html.py` |
| 契约测试 | `tests/gateway_hardening_tests.py` |

缺口：

1. 没有显式 `auth_ready` / `points_ready` / `generate_ready` 字段（运维只能猜一个 tag）。
2. `account_risk_status` 几乎只返回 `clean|invalid`，`risk_control` 健康分支基本死代码。
3. 健康判定与余额/能力逻辑耦合在 `server.py`，难单测。

## 切片计划

### Slice 1 — 模型 + 判定（本轮）

**交付**

1. 新增 `gateway/account_health.py`（纯函数，无 DB/HTTP）：
   - 输入：account dict/row 视图 + `now` + 可选配置阈值
   - 输出：
     - 兼容字段：`health_status`、`risk_status`、`balance_status`、`cooling`、`cooldown_remaining_seconds`
     - 新字段：`auth_ready: bool`、`points_ready: bool`、`generate_ready: bool`
   - 语义（固定，写进 docstring + 测试）：
     - `auth_ready`：`status in {verified,active}` 且 `ouid`/`ouss` 非空
     - `points_ready`：`balance_status` 为 `ok`（余额已知且 ≥ 现有 low 阈值；`unknown` 视为不可信 → false，与「低余额隔离」一致；若现网选号对 unknown 放行，则 `points_ready` 对 unknown=true 以匹配现有 `account_has_sufficient_balance` 行为——**以实现时对照 `account_has_sufficient_balance` 为准，优先不改变选号语义**）
     - `generate_ready`：`auth_ready` 且非冷却 且 `health` 最终可为 `healthy` 的同类条件 且具备可调度 capability（调用方传入 `has_schedulable_capability` 或在模块内接受 caps 布尔）
   - **禁止**改变现有 `health_status` 枚举值集合与字符串（保持 Admin/API 兼容）。

2. `server.py`：
   - `account_health_status` / `account_risk_status` / `account_balance_status` / cooldown helpers / `account_pool_summary` 改为委托新模块，或 thin wrapper。
   - `/api/accounts` 序列化与 `/v1/accounts/status`（及 metrics 若已有 healthy 计数）增加三态计数与 per-account 三 bool。
   - 若 `last_error` / failover code 表明风控（配置里的 `account_failover_error_codes` 或已有 quarantine 路径），`risk_status` 在冷却期内可报 `risk_control`（仅当不破坏现有 invalid 语义时）。**若判断会大改测试行为，先只加字段，风险细分可 defer。**

3. Admin（最小）：
   - 号池汇总显示 `generate_ready` 计数（可用现有 healthy 旁加一项，或把 pool-count 改为 generate_ready 并保留 healthy 文案说明）。
   - 账号行可显示三态小标记（auth/points/gen），**不要**重做表格布局。

4. 测试：
   - 新模块纯单测（表驱动：auth/points/gen 组合）。
   - 现有 `test_accounts_response_includes_health_summary_fields` / `test_gateway_account_status_reports_health_counts` 更新断言新字段，**旧 health_status 断言必须仍绿**。
   - 全量相关：`python -m unittest tests.gateway_hardening_tests -v` 中与 account/pool 相关用例；至少跑 hardening 中 health 相关 + 新测。

**明确不做（Slice 1）**

- 不改注册流水线 / Outlook 导入
- 不抽完整 registration 模块
- 不加重生成探针任务（`registration_require_generation_probe` 保持不动）
- 不改多 worker / 外部队列

### Slice 2 — 调度隔离（已完成 2026-07-24）

- [x] `select_generation_account` / `pick_account_for_generation` 显式 `generate_ready` 门闩
- [x] 503 details：`generate_ready_candidates` / `skipped_not_generate_ready` / `balance_misses`（error code 不变）
- [x] metrics 暴露 `accounts.generate_ready`（与 status API 同源）
- [ ] 失败分类 → 动作表可配置（YAGNI，仍用现有 `mark_account_failure`）

### Slice 3 — 运维面板（再下一轮）

- 失败原因 top N
- 可选批量探针（默认关）

## 验收（Slice 1）

- [x] `gateway/account_health.py` 存在且无 IO
- [x] API account item 含 `auth_ready` / `points_ready` / `generate_ready`
- [x] pool summary / accounts status 含对应计数
- [x] 既有 health_status 字符串行为兼容
- [x] 相关 unittest 通过（`tests.account_health_tests` + hardening health 用例，2026-07-24 复核 OK）

## 实现注意

- `server.py` ~10k 行：只改委托与序列化接线，禁止顺手重构周边。
- 配置阈值：优先复用现有 balance low（`<10`）与 cooldown 字段；新配置项除非必要否则不加。
- 导入：保持与现有 `gateway/*` 风格一致。
