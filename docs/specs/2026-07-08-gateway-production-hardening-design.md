# 生图生视频网关生产化加固设计

## 目标

把现有 OreateAI Gateway 从“能转发生成请求”提升到“适合作为生图/生视频网关”的基础形态。核心目标是：

- 调用方能清楚知道可用模型、分辨率、比例、视频时长和场景。
- 网关能拒绝明显无效或超出能力目录的参数，减少上游无效调用和扣费风险。
- API Key 能配置限流和每日配额，保护成本和服务稳定性。
- 调用方能安全重试请求，不因网络重试重复创建任务。
- 任务、成本、账号选择和错误原因可审计。
- 账号池选择不再只取最新账号，而是考虑模型能力、冷却时间、失败率和最近使用时间。

## 当前状态

已具备：

- `POST /v1/generate`：API Key 保护的图片/视频生成入口。
- `GET /v1/capabilities`：API Key 保护的模型能力目录。
- `GET /v1/tasks` 和 `GET /v1/task/{task_id}`：按 API Key 隔离的任务查询。
- 后台 API Key 管理、使用日志、账号导入、手动生成、模型能力刷新。
- `usage_log.task_id` 已存在，可以把 API Key 调用和任务关联起来。

实现更新（2026-07-09）：

- 本设计列出的核心加固项已经落地：`/v1/*` 错误 envelope、模型能力白名单校验、API Key 限流/每日配额、`Idempotency-Key`、账号冷却调度、任务详情别名和审计字段。
- 后续实现超出了原始范围，已经补齐网页一致的 `create_chat -> /oreate/sse/stream -> getmessagelist` 结果水合路径。
- 已用真实账号验证最小 1K 生图、基础文本生视频，以及上传图生视频 `text_or_image`。文本生视频和上传图生视频均可水合出 Oreate CDN MP4。
- 仍未完成生产等价验证的是上传类高级视频场景 `reference`、`frame_based`、`motion`，以及异步任务生命周期、失败重试/取消和自动补号。

本设计立项时的主要缺口：

- `/v1/generate` 目前只做基本 `kind` 分支，未基于能力目录校验模型、分辨率、比例、时长、场景。
- 错误响应格式仍由 FastAPI 默认输出，不利于调用方稳定处理。
- API Key 没有限流和每日配额。
- 没有 `Idempotency-Key`，调用方重试可能重复创建生成任务。
- 账号调度没有冷却和失败率，容易持续选择坏账号。
- 使用日志缺少模型、分辨率、时长、点数成本、错误码等审计字段。
- 任务查询接口命名不统一，已有 `/v1/task/{task_id}`，更常规形式应补 `/v1/tasks/{task_id}` 并保留旧接口兼容。

## 范围

### 本轮实现

1. `/v1/*` 统一错误响应。
2. 生成参数白名单校验和点数成本估算。
3. API Key 限流和每日请求/点数配额。
4. `Idempotency-Key` 幂等重试。
5. 账号调度和失败冷却。
6. 任务详情别名 `/v1/tasks/{task_id}` 和审计字段补全。
7. 后台 API Key 策略配置和使用日志增强。

### 本轮不实现

- 原始设计未计划反解 Oreate 结果流和真实成品 URL；后续实现已经补齐 SSE 解析、历史消息轮询、水合真实图片/视频 CDN URL，并通过真实账号验证基础生图、生视频和上传图生视频 `text_or_image`。
- 不做分布式限流。当前项目是单进程 FastAPI + SQLite，先做单实例可用方案。
- 不引入 Redis、Celery 或独立任务队列。
- 不做复杂计费规则配置后台。点数成本先从能力目录估算，无法估算时记为 `null`。
- 不移除现有接口。旧的 `/v1/task/{task_id}` 保留兼容。

## API 设计

### 统一错误格式

所有 `/v1/*` 网关接口失败时返回：

```json
{
  "ok": false,
  "error": {
    "code": "INVALID_MODEL",
    "message": "model_name is not supported for image generation",
    "details": {
      "field": "model_name",
      "allowed": ["Google Nano Banana 2"]
    }
  },
  "request_id": "req_abc123"
}
```

HTTP 状态码规则：

- `400`：请求 JSON 或基础参数非法。
- `401`：缺失或无效 API Key。
- `403`：API Key 被禁用或超出授权策略。
- `409`：同一个 `Idempotency-Key` 对应的请求体不一致。
- `422`：模型能力校验失败。
- `429`：限流或每日配额触发。
- `503`：没有可用账号或上游不可用。

后台 `/api/*` 可以继续使用 FastAPI 默认错误结构，避免一次性扩大前端改造面。

### 生成请求

现有接口保持：

`POST /v1/generate`

新增请求头：

```text
Idempotency-Key: optional-client-generated-key
X-Request-ID: optional-client-request-id
```

请求体仍兼容：

```json
{
  "kind": "video",
  "prompt": "a cinematic product shot",
  "model_name": "Seedance 2.0 Mini",
  "ratio": "16:9",
  "resolution": "720",
  "duration": 5,
  "scene_id": "text_or_image",
  "account_id": null
}
```

成功响应新增审计字段：

```json
{
  "ok": true,
  "task_id": 123,
  "account_id": 5,
  "request_id": "req_abc123",
  "idempotent_replay": false,
  "estimated_point_cost": 20,
  "response": {}
}
```

幂等重放响应：

```json
{
  "ok": true,
  "task_id": 123,
  "account_id": 5,
  "request_id": "req_abc123",
  "idempotent_replay": true,
  "estimated_point_cost": 20,
  "response": {}
}
```

### 任务详情

新增标准别名：

`GET /v1/tasks/{task_id}`

保留兼容：

`GET /v1/task/{task_id}`

响应结构：

```json
{
  "ok": true,
  "task": {
    "id": 123,
    "kind": "video",
    "status": "created",
    "account_id": 5,
    "model_name": "Seedance 2.0 Mini",
    "resolution": "720",
    "ratio": "16:9",
    "duration": 5,
    "scene_id": "text_or_image",
    "estimated_point_cost": 20,
    "created_at": 1720000000.0,
    "updated_at": 1720000000.0
  }
}
```

## 参数校验规则

### 基础校验

- `kind` 必须是 `image` 或 `video`。
- `prompt` 必须是非空字符串，最大长度默认 `4000`。
- `duration` 如果提供必须是正整数。
- `account_id` 如果提供，只能选择当前可用状态账号。

### 能力目录校验

图片：

- `model_name` 必须存在于 `image.models[].name`。
- `resolution` 必须存在于该模型 `resolutions`，如果能力目录为空则回退配置默认值。
- `ratio` 必须存在于该模型 `ratios`，如果能力目录为空则回退配置默认值。

视频：

- `model_name` 必须存在于 `video.models[].name`。
- `resolution` 必须存在于该模型 `resolutions`。
- `ratio` 必须存在于该模型 `ratios`。
- `duration` 必须存在于该模型 `durations`。
- `scene_id` 必须存在于 `video.scenes[].scene_id`。

如果能力目录完全缺失：

- `POST /v1/generate` 返回 `503 CAPABILITIES_UNAVAILABLE`，提示管理员刷新模型能力。
- 后台手动生成可继续使用已存在的默认配置，但要显示能力缺失提示。

## 点数成本估算

图片：

- 从模型 `point_cost` 中按 `resolution` 匹配。
- 若没有精确匹配，记录 `estimated_point_cost = null`。

视频：

- 优先从 `point_cost_image` 或 `point_cost_reference` 中按 `duration`、`resolution` 匹配。
- 当前请求没有图生/参考图字段，默认使用 `point_cost_image`。
- 若无法匹配，记录 `estimated_point_cost = null`。

点数估算只用于审计和配额预检，不代表上游最终实际扣费。

## API Key 策略

在 `api_keys` 增加策略字段：

- `rate_limit_per_minute`：每分钟请求数，`0` 或 `null` 表示使用全局默认。
- `daily_request_limit`：每日请求数，`0` 或 `null` 表示不限制。
- `daily_point_limit`：每日估算点数，`0` 或 `null` 表示不限制。

默认策略来自 `config.json`：

```json
{
  "gateway": {
    "default_rate_limit_per_minute": 60,
    "default_daily_request_limit": 0,
    "default_daily_point_limit": 0,
    "idempotency_ttl_hours": 24,
    "account_cooldown_seconds": 300,
    "prompt_max_length": 4000
  }
}
```

限流算法：

- 单实例内存滑动窗口，键为 `api_key_id`。
- 只保护当前进程，不保证多进程一致性。
- 每日配额从 `usage_log` 按当天窗口聚合，SQLite 持久化。

触发策略：

- 超过每分钟请求数：`429 RATE_LIMITED`。
- 超过每日请求数：`429 DAILY_REQUEST_LIMIT_EXCEEDED`。
- 超过每日估算点数：`429 DAILY_POINT_LIMIT_EXCEEDED`。

## 幂等设计

新增表 `idempotency_keys`：

```sql
CREATE TABLE IF NOT EXISTS idempotency_keys (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  api_key_id INTEGER NOT NULL,
  idempotency_key TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  status_code INTEGER NOT NULL,
  response_json TEXT NOT NULL,
  task_id INTEGER,
  created_at REAL NOT NULL,
  UNIQUE(api_key_id, idempotency_key)
)
```

行为：

- 没有 `Idempotency-Key`：按普通请求处理。
- 首次提交：创建任务后保存响应。
- 相同 API Key + 相同 key + 相同请求 hash：直接返回已保存响应，`idempotent_replay = true`。
- 相同 API Key + 相同 key + 不同请求 hash：返回 `409 IDEMPOTENCY_KEY_CONFLICT`。
- 定期清理超过 `gateway.idempotency_ttl_hours` 的记录。

请求 hash 只包含影响生成的业务字段，不包含 `X-Request-ID`。

## 账号调度

在 `accounts` 增加调度字段：

- `last_used_at REAL`
- `failure_count INTEGER NOT NULL DEFAULT 0`
- `cooldown_until REAL`

选择账号时：

1. 状态必须是 `verified` 或 `active`。
2. 必须有对应 `kind` 的能力缓存。
3. `cooldown_until` 为空或已过期。
4. 如果指定 `account_id`，也必须满足能力和冷却条件。
5. 排序：`failure_count ASC`, `last_used_at ASC`, `updated_at DESC`, `id ASC`。

调用上游成功：

- 更新 `last_used_at`。
- `failure_count` 归零。
- 清空 `last_error` 和 `cooldown_until`。

调用上游失败：

- `failure_count += 1`。
- 写入 `last_error`。
- 设置 `cooldown_until = now + gateway.account_cooldown_seconds * min(failure_count, 6)`。

## 审计字段

在 `usage_log` 增加字段：

- `request_id TEXT`
- `idempotency_key TEXT`
- `model_name TEXT`
- `resolution TEXT`
- `ratio TEXT`
- `duration INTEGER`
- `scene_id TEXT`
- `estimated_point_cost INTEGER`
- `error_code TEXT`
- `status_code INTEGER`

任务表 `tasks` 继续保存完整 `payload_json` 和 `response_json`，不重复扩展过多字段。任务详情接口从 `payload_json` 解析模型参数，从 `usage_log` 合并审计字段。

## 后台 UI

API Keys 页面新增：

- 每分钟限流。
- 每日请求限制。
- 每日点数限制。
- Key 创建和更新策略。

用量日志新增展示：

- 模型、分辨率、比例、时长、场景。
- 估算点数。
- 错误码和状态码。
- 请求 ID 和幂等 key 预览。

生成页：

- 当能力目录缺失时明确显示刷新提示。
- 当参数不符合能力目录时，前端阻止提交并显示可选项。

## 回归风险与兼容策略

- 保留现有 `/v1/generate` 请求体字段。
- 保留现有 `/v1/task/{task_id}`。
- 成功响应只增加字段，不移除旧字段。
- 后台接口继续沿用当前登录 token。
- 数据库迁移使用 `PRAGMA table_info` 判断列是否存在，避免重复 `ALTER TABLE`。
- 任何白名单校验失败必须发生在调用 Oreate 之前，避免无效扣费。

## 验收标准

- 无 API Key 的 `/v1/*` 仍返回 401。
- 无模型能力缓存时，网关生成返回明确 `CAPABILITIES_UNAVAILABLE`。
- 非法模型、分辨率、比例、时长、场景返回 422，且不调用 `CLIENT.create_chat`。
- 相同 `Idempotency-Key` 重试不会创建第二个任务。
- 相同 `Idempotency-Key` 但请求体不同返回 409。
- 超出限流或每日配额返回 429。
- 上游失败会让账号进入冷却，后续调度跳过冷却账号。
- 任务详情只能被创建该任务的 API Key 查询。
- 后台可以创建和修改 API Key 策略。
- 全量测试、Python 编译、JS 语法解析和 `git diff --check` 通过。

截至 2026-07-09，以上验收标准已在实现提交中通过自动化测试覆盖；真实上游验证额外证明了结果 URL 水合路径。剩余工作应作为后续版本验收：高级上传场景真实成功样本、异步任务生命周期、重试/取消和自动补号。
