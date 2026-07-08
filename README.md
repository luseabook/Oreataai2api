# OreateAI Gateway / Pool Manager

当前状态：
- 已打通 Oreate 登录协议
- 已打通图片/视频配置接口
- 已打通 `/oreate/create/chat` 的 image/video 提交入口
- 已实现基础管理服务：账号导入、号池存储、图片/视频提交 API
- 已新增 `/v1/capabilities` 模型能力发现接口，返回图片/视频模型、描述、分辨率、比例、时长和视频场景
- 已新增模型参数白名单校验、API Key 限流/配额、`Idempotency-Key` 幂等和成本审计
- 已新增 `/v1/tasks/{task_id}` 标准任务详情接口，旧 `/v1/task/{task_id}` 仍保留兼容
- 后台支持独立修改管理员账号密码，修改后强制重新登录
- 结果流 / 资源 URL 抽取仍需继续补完
- 自动注册（YYDS 邮箱）链路尚未完成

## 文件
- `server.py` — FastAPI 服务，SQLite 持久化，管理页 `/admin`
- `config.example.json` — 配置模板
- `config.json` — 实际配置（首次运行可从 example 复制）
- `accounts.db` — SQLite 号池数据库（运行后自动生成）

## 运行
```bash
pip install -r requirements.txt
cp config.example.json config.json
# 编辑 config.json，填入非默认管理员密码和 YYDS API Key
python server.py
```

默认监听：
- `http://127.0.0.1:8890`
- 管理页：`http://127.0.0.1:8890/admin`

管理页和 `/api/*` 管理接口需要先用 `config.json` 中的管理员账号登录。`/v1/*` 网关接口继续使用 `Authorization: Bearer <API Key>`。
占位密码（如 `admin123`、`CHANGE_ME`）会被拒绝登录；已有 `config.json` 也需要改掉旧默认密码。
管理员账号密码请在后台“设置 -> 管理员账号”中修改，通用设置接口不会接受 `server.admin_username` 或 `server.admin_password` 更新。

## 关键协议结论

### 登录
1. `GET /passport/api/getticket`
2. 使用返回 `pk` 做 RSA PKCS#1 v1.5 加密密码并 base64
3. `POST /passport/api/emaillogin`

最小成功 body：
```json
{
  "email": "<email>",
  "password": "<rsa_base64>",
  "ticketID": "<ticketID>",
  "fr": "main",
  "jt": ""
}
```

### 生图提交
`POST /oreate/create/chat`
```json
{
  "docId": "",
  "content": "a cute corgi astronaut on the moon, cinematic lighting",
  "chatMode": "aiImage",
  "modelName": "Google Nano Banana 2",
  "ratio": "16:9",
  "resolution": "4K",
  "jt": ""
}
```

### 生视频提交
`POST /oreate/create/chat`
```json
{
  "docId": "",
  "content": "a corgi astronaut gently waving on the moon",
  "chatMode": "aiVideo",
  "sceneId": "text_or_image",
  "modelName": "Seedance 2.0 Mini",
  "duration": 5,
  "resolution": "480",
  "ratio": "16:9",
  "jt": ""
}
```

### 网关能力发现
`GET /v1/capabilities`

请求头：
```text
Authorization: Bearer <API Key>
```

响应会规范化返回：
- `image.models[]`：模型名称、描述、支持分辨率、比例和点数成本。
- `video.models[]`：模型名称、描述、支持时长、分辨率、比例、音频/尺寸能力和点数成本。
- `video.scenes[]`：视频场景 ID、名称、描述和图标。

后台“生成”页会使用这些能力填充模型、分辨率、比例、时长和场景下拉项；后台“设置 -> 模型能力”可手动刷新账号缓存。

### 网关生成
`POST /v1/generate`

请求头：
```text
Authorization: Bearer <API Key>
Idempotency-Key: <可选，客户端生成的幂等键>
X-Request-ID: <可选，客户端请求 ID>
```

请求体继续兼容：
```json
{
  "kind": "image",
  "prompt": "hello",
  "model_name": "Google Nano Banana 2",
  "resolution": "4K",
  "ratio": "16:9"
}
```

网关会基于 `/v1/capabilities` 的能力目录校验模型、分辨率、比例、视频时长和场景；非法参数会在调用 Oreate 前返回 `422`，避免无效扣费。成功响应包含 `request_id`、`idempotent_replay` 和 `estimated_point_cost`。

### 任务查询
- `GET /v1/tasks`：当前 API Key 的任务列表。
- `GET /v1/tasks/{task_id}`：标准任务详情接口。
- `GET /v1/task/{task_id}`：兼容旧接口。

任务详情会合并审计字段：模型、分辨率、比例、视频时长、场景、估算点数、请求 ID、幂等 key、错误码和状态码。

### 错误格式
`/v1/*` 网关接口使用稳定错误 envelope：

```json
{
  "ok": false,
  "error": {
    "code": "INVALID_RESOLUTION",
    "message": "resolution is not supported",
    "details": {}
  },
  "request_id": "req_xxx"
}
```

常见错误码：
- `UNAUTHORIZED`：缺少或无效 API Key。
- `CAPABILITIES_UNAVAILABLE`：没有模型能力缓存，需要后台刷新。
- `INVALID_MODEL` / `INVALID_RESOLUTION` / `INVALID_RATIO` / `INVALID_DURATION` / `INVALID_SCENE`：请求参数不在能力目录中。
- `IDEMPOTENCY_KEY_CONFLICT`：同一个 `Idempotency-Key` 被不同请求体复用。
- `RATE_LIMITED` / `DAILY_REQUEST_LIMIT_EXCEEDED` / `DAILY_POINT_LIMIT_EXCEEDED`：API Key 策略限制触发。
- `UPSTREAM_ERROR`：Oreate 上游调用失败，账号会进入冷却。

### API Key 策略
后台 API Keys 页面可配置：
- `rate_limit_per_minute`：每分钟请求数，空值使用全局默认。
- `daily_request_limit`：每日请求数，空值或 `0` 表示不限制。
- `daily_point_limit`：每日估算点数，空值或 `0` 表示不限制。

全局默认配置：
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

## 当前缺口
- 自动注册：待补 `/passport/api/emailsignupin` + YYDS 收信 + `/passport/api/emailregisterconfirm`
- 结果流：已确认前端存在 SSE 管理器，但真实结果 URL / groupId 映射未完成
- 号池维护：基础结构已搭好，自动补号逻辑待实现
- 网关结果：仍需补充真实成品 URL / groupId 映射、结果轮询或 SSE 回传、失败任务重试/取消

## 下一步
1. 先补自动注册链路
2. 再补结果流定位
3. 最后补号池自动维护
