# OreateAI Gateway / Pool Manager

当前状态：
- 已打通 Oreate 登录协议
- 已打通图片/视频配置接口
- 已切换到网页一致的 `create_chat -> /oreate/sse/stream -> getmessagelist` 生成协议
- 已用真实账号验证最小 1K 生图：stream 成功返回并可从历史消息提取 Oreate CDN 图片 URL
- 已用真实账号验证基础文本生视频：视频 SSE 可持续 ping，最终 MP4 需要从历史消息轮询水合
- 已用真实账号验证上传图生视频 `text_or_image`：`/v1/uploads` 产物可进入视频生成并水合出 Oreate CDN MP4
- 已实现基础管理服务：账号导入、号池存储、图片/视频提交 API
- 已新增 `/v1/capabilities` 模型能力发现接口，返回图片/视频模型、描述、分辨率、比例、时长和视频场景
- 已新增模型参数白名单校验、API Key 限流/配额、`Idempotency-Key` 幂等和成本审计
- 已新增 `/v1/uploads`，按网页 BOS 上传协议返回可用于视频首尾帧、参考素材和动作模仿的附件对象
- 已新增 `/v1/tasks/{task_id}` 标准任务详情接口，旧 `/v1/task/{task_id}` 仍保留兼容
- 已完成 Phase 0 风险收口：reference/frame_based/motion 默认关闭，API Key 删除改为软删除，TLS verify 改为配置化
- 已完成 Phase 1 任务中心：`/v1/generate` 默认异步排队，支持任务详情、重试、取消和重新水合
- 后台支持独立修改管理员账号密码，修改后强制重新登录
- 已补充 SSE 事件解析、上游错误分类、账号健康分类和历史消息资源 URL 抽取
- 自动注册（YYDS 邮箱）链路尚未完成

## 文件
- `server.py` — FastAPI 服务，SQLite 持久化，管理页 `/admin`
- `config.example.json` — 配置模板
- `config.json` — 实际配置（首次运行可从 example 复制）
- `accounts.db` — SQLite 号池数据库（运行后自动生成）

## 运行

运行要求：
- Python 3.11 或更高版本。
- Node.js 18 或更高版本；`banti_jt_helper.js` 是生成请求的必要运行依赖。
- 必须配置独立保存的 Fernet 密钥 `OREATE_ENCRYPTION_KEY`，否则已有明文账号不会迁移，新账号也不能安全写入。

生成密钥（仅在受控终端执行，输出不要写入 Git、日志或普通备份）：

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Windows PowerShell 示例：

```powershell
$env:OREATE_ENCRYPTION_KEY = "<上一步生成的密钥>"
```

密钥必须和数据库备份分开保存；丢失密钥将无法解密账号凭据。

```bash
pip install -r requirements.txt
cp config.example.json config.json
# 编辑 config.json，填入非默认管理员密码和 YYDS API Key
python server.py
```

### 单应用 worker 部署边界

当前限流、调度唤醒和部分运行状态是进程内状态，因此一个网关实例必须只运行 **1 个应用 worker**。`python server.py` 已显式使用 `workers=1`；外部进程管理器也必须保持单 worker，例如：

```bash
uvicorn server:app --workers 1
gunicorn server:app --worker-class uvicorn.workers.UvicornWorker --workers 1
```

启动时会读取并交叉校验以下 worker 声明：

- `OREATE_APP_WORKERS=1`（网关的明确部署声明；旧的 `OREATE_WORKER_COUNT=1` 仍兼容）
- `WEB_CONCURRENCY=1`
- `GUNICORN_CMD_ARGS="--workers 1"`

任何大于 1、非法或互相冲突的声明都会在数据库初始化前拒绝启动。除声明校验外，进程还会持有与当前数据库绑定的 `<accounts.db>.worker.lock` 文件锁作为最终防线；即使漏配 worker 环境变量，同一个数据库也不能被第二个应用 worker 同时启动。服务管理器需要单独的运行目录时，可用 `OREATE_WORKER_LOCK_PATH` 覆盖锁文件位置。锁文件本身不含凭据，正常退出时会释放；若后台任务线程未能在 `gateway.worker_shutdown_timeout_seconds`（默认 30 秒）内停止，进程会保留锁直到退出，避免旧线程和新实例并行处理任务。

默认监听：
- `http://127.0.0.1:8890`
- 管理页：`http://127.0.0.1:8890/admin`

### 公网监听 / 反向代理 / TLS 边界

默认只建议绑定回环地址 `127.0.0.1`。如果要把 `server.host` 改成 `0.0.0.0`、公网 IP 或其他非回环地址，必须同时满足这三个显式声明：

- `deployment.allow_public_bind=true`
- `deployment.trust_reverse_proxy=true`
- `deployment.tls_terminated_by_proxy=true`

这三个开关是上线确认，不是功能开关。`/readyz` 会把“公网监听但未明确声明反向代理与 TLS 边界”判成不就绪，避免把管理面和网关直接裸露在明文 HTTP 或未审计入口上。推荐部署形态是：公网入口只放经过 TLS 的反向代理，网关进程自己仍保持单应用 worker，并且只接收来自该代理的流量。

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
真实网页协议不是把生成参数直接发给 `/oreate/create/chat`。该接口只创建 chat session：
```json
{
  "type": "aiImage",
  "docId": ""
}
```

随后提交到 `POST /oreate/sse/stream`，其中提示词在 `messages[0].content`，图片参数在 `imageConfig`：
```json
{
  "chatType": "aiImage",
  "messages": [{"role": "user", "content": "a cute corgi astronaut on the moon", "attachments": []}],
  "imageConfig": {
    "modelName": "Google Nano Banana 2",
    "ratio": "16:9",
    "resolution": "4K"
  }
}
```

### 生视频提交
同样先创建 chat session：
```json
{
  "type": "aiVideo",
  "docId": ""
}
```

再提交 `POST /oreate/sse/stream`。视频参数必须是网页的场景嵌套结构，不是扁平字段：
```json
{
  "chatType": "aiVideo",
  "messages": [{"role": "user", "content": "a corgi astronaut gently waving on the moon", "attachments": []}],
  "videoConfig": {
    "modelName": "Seedance 2.0 Mini",
    "ratio": "16:9",
    "resolution": "480",
    "duration": 5,
    "isAudio": false,
    "scene": "text_or_image",
    "textOrImage": {"image": ""}
  }
}
```

视频生成和图片生成有一个关键差异：网页端视频 SSE 可能长时间只返回 `start/ping`，即使任务已经成功提交，也不一定会发送 `end`。网关现在在视频流进入该状态后转为轮询 `/oreate/memory/getmessagelist?chatID=<chatId>`，从历史消息里的 `<video src="...mp4">` 提取最终 CDN 地址。

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

### new-api / sub2api 媒体上游兼容

本项目可作为图片、图片编辑和视频生成上游，不提供虚假的聊天补全响应。可用的 OpenAI 兼容接口包括：

- `GET /v1/models`：返回当前 Key 有权使用的模型。
- `GET /v1/models/{model}`：查询单个可用模型。
- `POST /v1/images/generations`：图片生成。
- `POST /v1/images/edits`：`multipart/form-data` 图片编辑，上传字段为 `image`、`image[]` 或 `image[0]`。
- `POST /v1/videos` 与 `POST /v1/videos/generations`：视频生成。

接入地址规则不同，不能混填：

- **new-api**：渠道类型选 OpenAI，基础地址填写站点根地址，例如 `https://example.com`。new-api 会自行请求 `/v1/models` 和媒体路径；渠道测试请选择“图片生成”，不要使用默认聊天测试。
- **sub2api**：OpenAI API Key 上游的 `base_url` 填写到 `/v1`，例如 `https://example.com/v1`。sub2api 会在其后追加 `/images/generations`、`/images/edits` 或 `/videos/generations`。

当前明确边界：图片接口仅支持 `n=1`，不支持 `stream=true`；图片编辑暂不支持 `mask` 蒙版。所有不支持的参数都会在创建任务或上传素材前返回 OpenAI 风格错误。

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

上传类视频场景先调用 `/v1/uploads`，再把返回的 `attachment` 放入对应字段：
```json
{
  "kind": "video",
  "prompt": "make the reference material cinematic",
  "model_name": "Seedance 2.0 Mini",
  "ratio": "16:9",
  "resolution": "480",
  "duration": 5,
  "scene_id": "reference",
  "reference_images": [{"fileName": "ref", "fileExt": "png", "originSize": 1234, "object": "uploads/ref.png"}],
  "reference_videos": [{"fileName": "ref", "fileExt": "mp4", "originSize": 9876, "object": "uploads/ref.mp4", "videoDurationSec": 4}],
  "ref_duration": "2-5",
  "ref_total_duration": 4,
  "keep_original_sound": true
}
```

网关会基于 `/v1/capabilities` 的能力目录校验模型、分辨率、比例、视频时长和场景；非法参数会在调用 Oreate 前返回 `422`，避免无效扣费。成功响应包含 `request_id`、`idempotent_replay`、`estimated_point_cost`、`assets` 和上游 `response` 摘要。

`jt` 由本地 `banti_jt_helper.js` 恢复，Python 只负责 HTTP 协议、账号池、SSE 解析和结果水合；生产路径不依赖浏览器或浏览器配置文件。

视频请求使用网页视频页 referer：`/home/vertical/aiVideo/zh`。当视频 SSE 只持续 ping 或读超时时，任务不会被误判为失败；网关会继续按 chatId 轮询历史消息，拿到视频 URL 后返回 `completed`，超时仍无资产则返回 `submitted`。

### 网关上传
`POST /v1/uploads`

请求头：
```text
Authorization: Bearer <API Key>
Content-Type: multipart/form-data
```

表单字段：
- `file`：要上传的图片或视频。
- `account_id`：可选，指定用于换取上传 token 的账号。

响应中的 `attachment` 可直接放入：
- `image`：`text_or_image` 图生视频。
- `first_frame` / `last_frame`：首尾帧视频。
- `reference_images[]` / `reference_videos[]`：参考素材视频。
- `character_image` / `motion_video`：动作模仿。

这些字段必须使用 `/v1/uploads` 返回的对象，至少包含 `object`/`bosUrl` 路径；本地文件路径或只有文件名的占位对象会被本地拒绝，不会继续打上游。

`message_attachment` 是网页 `nke(files)` 格式，网关生成时会自动从上述字段重建 `messages[0].attachments`。

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
- `MISSING_VIDEO_ATTACHMENT`：视频场景缺少必要的上传对象，例如首尾帧、参考素材或动作视频。
- `UPLOAD_FAILED`：BOS 上传协议失败，账号会按上游错误类型分类处理。
- `IDEMPOTENCY_KEY_CONFLICT`：同一个 `Idempotency-Key` 被不同请求体复用。
- `RATE_LIMITED` / `DAILY_REQUEST_LIMIT_EXCEEDED` / `DAILY_POINT_LIMIT_EXCEEDED`：API Key 策略限制触发。
- `UPSTREAM_ERROR`：Oreate 上游调用失败，账号会按错误类型进入冷却或失效。
- 上游 `200002 params error`：`/oreate/sse/stream` 参数合同未通过，不是额度不足；通常不会扣点。已确认关键原因包括缺少网页 `ZCe` 用户镜像字段（`vip/reg_ts`）或 Banti `__bid_n`。

账号健康分类：
- `200001`：登录态失效，账号标记为 `invalid`，未指定固定账号时自动换号重试。
- `200002`：协议参数被拒绝，账号保留但进入冷却。
- `212361`：生成运行环境触发上游风控。该错误不会增加账号失败次数、不会冷却或隔离账号，也不会自动轮换号池；生产环境应启用真实 Chromium 工作节点。
- `110012`：历史消息未生成或未持久化，只记录警告，不惩罚账号池。

自动换号在同一个任务内执行，不会新增 API Key 请求计数；每次账号尝试都会写入任务尝试记录。默认最多尝试 5 个不同账号，参数错误等请求级失败不会遍历号池。

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
    "account_risk_quarantine_seconds": 3600,
    "account_failover_max_attempts": 5,
    "account_failover_error_codes": ["200001"],
    "prompt_max_length": 4000
  },
  "oreate": {
    "video_stream_wait_seconds": 60,
    "video_stream_read_timeout_seconds": 20,
    "video_hydration_timeout_seconds": 600,
    "video_hydration_poll_interval_seconds": 10
  }
}
```

## 当前缺口
- 自动注册：待补 `/passport/api/emailsignupin` + YYDS 收信 + `/passport/api/emailregisterconfirm`
- 上传类视频高级场景回归：文本转视频和上传图生视频 `text_or_image` 已用真实账号验证成功；`reference`、`frame_based`、`motion` 仍需要单独低成本实测
- 号池维护：支持批量积分检查、低成本真实图片生成探针、失效账号隔离和健康账号自动补充；遇到 `212361` 会立即停止批量探测并保留账号，避免把网关环境问题误判为账号问题；新注册账号通过真实生成验证后才会进入可调度号池

生产环境推荐在 `oreate` 配置中启用真实浏览器生成工作节点：

```json
{
  "browser_worker_enabled": true,
  "browser_worker_node": "node",
  "browser_worker_timeout_seconds": 150,
  "browser_worker_readiness_timeout_seconds": 60,
  "browser_worker_node_modules": "/var/lib/oreateai/browser-worker/node_modules",
  "chromium_executable": "/usr/bin/chromium-browser"
}
```

工作节点依赖 `puppeteer-core`，账号 Cookie 通过子进程标准输入传递，不会出现在进程命令行中。
- 网关结果：已支持同步解析 SSE 和历史消息资源 URL，后续可补异步任务轮询、失败任务重试/取消

## 下一步
1. 先补自动注册链路
2. 低成本实测上传类视频高级场景：`reference`、`frame_based`、`motion`
3. 补异步任务轮询、失败任务重试/取消和号池自动维护
