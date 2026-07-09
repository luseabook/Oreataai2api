# 生图生视频网关审查后修复计划与目标模式提示词

## 1. 当前结论

当前项目已经从“能跑的原型”推进到“接近受控内测网关”，但还不能称为合格生产网关。

本轮审查结果是 `REQUEST CHANGES`。原因不是测试失败，而是仍有几个会直接影响安全、成本对账和部署判断的上线风险。

已验证通过：

- `python -m unittest discover -s tests -p "*_tests.py"`：94 tests 通过。
- `python -m py_compile server.py banti_token_generator.py`：通过。
- 后台内嵌 JS 解析：通过。
- `git diff --check`：通过。

但是：测试通过不等于生产可放行。下面 4 个问题必须先修。

## 2. 必须修复的问题

### P0-1：自动注册结果仍可能泄漏 Oreate cookies

现象：

- `confirm_email_register()` 会返回 `cookies`。
- 自动注册流程把 `confirm` 放进 `verification` 和 `trace`。
- `public_registration_result()` 目前只脱敏 `password`、`token`、`tokenID`、`jt`，没有脱敏 `cookies`、`OUID`、`ouss`。

影响：

- 管理后台调用 `/api/register/one` 或 `/api/register/batch` 时，响应中可能出现 Oreate 登录态。
- 这会破坏“后台接口不泄漏敏感信息”的安全承诺。

修复要求：

- `public_registration_result()` 必须递归脱敏：
  - `cookies`
  - `cookie`
  - `OUID`
  - `ouss`
  - `session`
  - `sessionkey`
  - `accessToken`
- 回归测试必须构造包含 `cookies: {OUID, ouss}` 的注册结果，确认输出不含真实值。

验收：

- `/api/register/one` 返回中不能出现真实 `OUID`、`ouss`、mail token、tokenID、密码。
- `trace` 和 `verification.confirm` 内也不能漏。

### P0-2：失败扣点仍然无法对账

现象：

- 成功路径会采集 `balance_before` 和 `balance_after`。
- 但上游提交后如果抛出 `UpstreamGenerationError`，异常分支直接写 `actual_point_cost=None`。
- 这会漏掉 `100003` 等“失败但扣点”的情况。

影响：

- 成本页看不到失败扣点。
- 账号点数会实际减少，但 usage/task 不记录，无法向客户或自己对账。

修复要求：

- 在生成/水合失败异常分支也尝试抓取失败后的账号余额。
- 如果任务上已有 `balance_before_*`，失败后应保存 `balance_after_*`。
- 计算 `actual_point_cost = max(0, before_rest_point - after_rest_point)`，即使任务状态是 `failed`。
- `usage_log.actual_point_cost` 和 `tasks.actual_point_cost` 都要更新。
- 对失败扣点任务保留 `error_code`，便于后台标红。

验收：

- 模拟 `UpstreamGenerationError(code=100003)` 且余额从 100 变 90，任务应为 `failed`，`actual_point_cost=10`。
- usage log 中同样记录 `actual_point_cost=10` 和 `error_code=100003`。

### P0-3：`readyz` 可能误报可用

现象：

- `account_pool_summary()` 对所有账号都计算 `health_status`。
- `new`、`disabled` 等不可调度账号可能被算进 `healthy`。
- `/readyz` 只检查 `summary["healthy"] > 0`。

影响：

- 没有真正可调度账号时，部署系统仍可能认为服务 ready。
- 外部流量进来后才发现无账号可用。

修复要求：

- `healthy` 必须只统计可调度账号：`status in ('verified', 'active')`。
- `readyz` 应至少检查：
  - DB 可写。
  - 配置可读。
  - 至少 1 个 `verified/active` 且未冷却、未风控、能力存在的账号。
- 如果没有可调度账号，应返回 `503`。

验收：

- 只有 `new` 账号时，`/readyz` 返回 `503`。
- 有 `verified` 且具备模型能力的账号时，`/readyz` 返回 `200`。

### P0-4：恢复备份可能恢复旧 admin session

现象：

- 备份会打包整个 `accounts.db`。
- 恢复时整库覆盖。
- `admin_sessions` 属于运行态安全数据，如果一起恢复，旧后台 token 可能重新有效。

影响：

- 已经退出、过期或本不该恢复的后台会话可能被重新带回来。
- 这是后台安全边界问题。

修复要求：

- 恢复备份后必须清空或 revoke 所有 `admin_sessions`。
- 同时清空内存 `ADMIN_TOKENS`。
- 恢复响应应明确提示：需要重新登录。

验收：

- 备份中存在未过期 admin session，恢复后该 token 访问 `/api/admin/settings` 必须返回 `401`。
- 恢复完成后新登录可以正常访问后台。

## 3. 修复顺序

推荐顺序：

1. 先修敏感信息脱敏。
2. 再修失败扣点对账。
3. 再修 `readyz` 健康统计。
4. 最后修备份恢复会话清理。

原因：

- 脱敏和失败扣点是最高风险。
- `readyz` 是部署风险。
- 备份恢复风险触发频率低，但必须在合格网关前补齐。

## 4. 修完后的状态判断

修完以上 4 项后，可以称为：

- 受控内测可用网关。
- 可继续做小流量自用或内部调用。

仍不能立刻称为完整生产级网关，剩余中长期事项包括：

- API Key 客户权限范围：allowed kinds/models/scenes/uploads。
- 高级视频场景 `reference`、`frame_based`、`motion` 的真实成功验证。
- 账号密码、OUID、ouss 加密存储。
- 成本报表按客户、Key、账号、模型、失败扣点拆账。
- 任务/用量后台分页、筛选、搜索。
- 备份恢复演练文档。

## 5. 新窗口目标模式提示词

把下面这段直接发到新窗口：

```text
目标：修复当前生图生视频网关审查中发现的 4 个上线阻塞问题，让项目达到“受控内测可用网关”的标准。

先读取这些文档并作为事实来源：
- docs/plans/2026-07-09-production-image-video-gateway-plan.md
- docs/plans/2026-07-09-gateway-gap-analysis-and-target-prompt.md
- docs/plans/2026-07-10-gateway-review-fix-plan-and-goal-prompt.md

本轮只修 4 个问题，不做无关重构：

1. 自动注册结果敏感信息脱敏不完整
   - public_registration_result 必须递归脱敏 cookies、cookie、OUID、ouss、session、sessionkey、accessToken。
   - /api/register/one 和 /api/register/batch 不能返回真实密码、mail token、tokenID、Oreate cookies。
   - 加回归测试，构造 cookies: {OUID, ouss}，确认输出不含真实值。

2. 失败扣点无法对账
   - UpstreamGenerationError 和普通异常分支也要尝试抓取 balance_after。
   - 如果已有 balance_before，则失败任务也要计算 actual_point_cost。
   - tasks 和 usage_log 都要写 actual_point_cost。
   - 加测试：模拟 error_code=100003，余额 100 -> 90，任务 failed，但 actual_point_cost=10。

3. readyz 误报
   - healthy 只能统计 verified/active 且可调度账号。
   - 只有 new/disabled/invalid/cooling/低能力账号时，/readyz 必须返回 503。
   - 有 verified/active 且具备能力的账号时，/readyz 返回 200。

4. 恢复备份后旧 admin session 可能复活
   - restore 完成后清空或 revoke admin_sessions。
   - 同时清空 ADMIN_TOKENS。
   - 恢复响应提示需要重新登录。
   - 加测试：备份里有未过期 session，恢复后旧 token 访问后台应返回 401。

执行纪律：
- 先写/补测试，再改实现。
- 不要消耗真实账号额度。
- 不要改无关功能。
- 不要提交 config.json、accounts.db、cookie、token、真实账号信息。
- 如果发现代码里还有其他安全泄漏，只在同一敏感信息脱敏边界内修，不扩大范围。

完成后必须运行：
- python -m unittest discover -s tests -p "*_tests.py"
- python -m py_compile server.py banti_token_generator.py
- node -e 'const fs=require("fs"); const text=fs.readFileSync("server.py","utf8"); const html=text.match(/ADMIN_HTML = """([\s\S]*?)"""/)[1]; const script=html.match(/<script>([\s\S]*?)<\/script>/)[1]; new Function(script); console.log("js parse ok");'
- git diff --check

最后输出：
- 修改了哪些文件。
- 4 个问题分别如何修复。
- 测试结果。
- 剩余还不能称为完整生产级网关的事项。
```

