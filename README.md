# OreateAI Gateway / Pool Manager

当前状态：
- 已打通 Oreate 登录协议
- 已打通图片/视频配置接口
- 已切换到网页一致的 `create_chat -> /oreate/sse/stream -> getmessagelist` 生成协议
- 已用真实账号验证最小 1K 生图：stream 成功返回并可从历史消息提取 Oreate CDN 图片 URL
- 已实现基础管理服务：账号导入、号池存储、图片/视频提交 API
- 已新增 `/v1/capabilities` 模型能力发现接口，返回图片/视频模型、描述、分辨率、比例、时长和视频场景
- 已新增模型参数白名单校验、API Key 限流/配额、`Idempotency-Key` 幂等和成本审计
- 已新增 `/v1/uploads`，按网页 BOS 上传协议返回可用于视频首尾帧、参考素材和动作模仿的附件对象
- 已新增 `/v1/tasks/{task_id}` 标准任务详情接口，旧 `/v1/task/{task_id}` 仍保留兼容
- 后台支持独立修改管理员账号密码，修改后强制重新登录
- 已补充 SSE 事件解析、上游错误分类、账号健康分类和历史消息资源 URL 抽取
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
- `200001`：登录态失效，账号标记为 `invalid`。
- `200002`：协议参数被拒绝，账号保留但进入冷却。
- `212361`：上游风控/垃圾用户，账号保留但进入冷却。
- `110012`：历史消息未生成或未持久化，只记录警告，不惩罚账号池。

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
- 号池维护：基础结构已搭好，自动补号逻辑待实现
- 网关结果：已支持同步解析 SSE 和历史消息资源 URL，后续可补异步任务轮询、失败任务重试/取消

## 下一步
1. 先补自动注册链路
2. 再补真实视频生成回归验证
3. 最后补号池自动维护
