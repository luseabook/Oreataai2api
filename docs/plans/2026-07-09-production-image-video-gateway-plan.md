# 生产级生图生视频网关完整计划

## 1. 背景与目标

用户目标：把当前项目从“能跑通 Oreate 生图/生视频协议的工具”升级为“合格的生图生视频网关”。

本文只定义计划和验收边界，不做代码实现。后续实现必须按本文拆分阶段推进，每阶段完成后更新本文状态或追加实施记录。

### 1.1 当前已具备的基础

代码与文档证据：

- `README.md` 已记录：最小 1K 生图、基础文本生视频、上传图生视频 `text_or_image` 均已用真实账号验证成功。
- `README.md` 已记录：已有 `/v1/capabilities`、`/v1/uploads`、`/v1/tasks/{task_id}`、参数白名单、API Key 限流/配额、幂等和审计。
- `server.py` 已实现 `/v1/generate`、`/v1/uploads`、`/v1/tasks`、`/v1/capabilities` 等网关接口。
- `server.py` 已实现后台账号、任务、API Key、用量日志、设置、模型能力刷新等基础页面。
- `docs/reverse/2026-07-08-oreate-web-generation-protocol.md` 已记录：高级上传类视频场景 `reference`、`frame_based`、`motion` 仍缺真实成功样本。

### 1.2 目标定义

“合格的生图生视频网关”至少要满足：

1. 调用方能稳定发现模型能力、提交任务、查询状态、拿到最终资源。
2. 后台能运营账号池、任务、成本、模型、API Key、上传素材和失败风险。
3. 视频任务不依赖长连接同步等待，必须有异步生命周期。
4. 账号、点数、失败、风控、扣费风险必须可观测、可干预。
5. 未真实验证的高级能力必须默认受控，不能直接开放给外部客户。
6. 安全、日志、备份、部署、监控达到单实例生产可用标准。

### 1.3 非目标

本阶段不追求：

- 多机分布式调度。
- 自研生图/生视频模型。
- 完全模拟 Oreate 前端 UI。
- 复杂 SaaS 多租户结算系统。
- 对外承诺所有 Oreate 网页高级功能 100% 等价，未验证场景必须标记为实验性。

## 2. 产品角色

### 2.1 API 调用方

需要：

- 查询可用模型、场景、分辨率、时长、价格。
- 提交图片/视频任务。
- 上传素材并复用上传对象。
- 查询任务状态和最终资源。
- 幂等重试。
- 收到 webhook 或轮询结果。
- 稳定错误码和可读错误原因。

### 2.2 后台运营者

需要：

- 看到任务是否成功、失败、卡住、扣点。
- 看到账号池健康、余额、冷却、登录态、失败率。
- 能手动重登、刷新余额、刷新能力、禁用/启用账号。
- 能手动重试任务、重新水合结果、取消任务。
- 能定位失败原因，避免盲目消耗点数。

### 2.3 管理员

需要：

- 管理 API Key、客户、配额、权限范围。
- 管理模型和视频场景是否开放。
- 查看成本报表和异常告警。
- 管理系统配置、密钥、备份和安全策略。

### 2.4 后续 AI 维护者

需要：

- 有清晰模块边界。
- 有验收标准。
- 有测试矩阵。
- 有真实账号验证记录。
- 有风险开关，避免误开放高成本能力。

## 3. 总体架构目标

### 3.1 目标模块树

```text
生产级网关
├── 网关 API
│   ├── 模型能力发现
│   ├── 任务提交
│   ├── 上传素材
│   ├── 任务查询
│   ├── 任务操作
│   └── webhook 回调
├── 异步任务系统
│   ├── 队列
│   ├── worker
│   ├── 状态机
│   ├── 结果水合
│   ├── 重试
│   └── 取消
├── 模型与场景治理
│   ├── 能力刷新
│   ├── 能力版本 diff
│   ├── 模型启停
│   ├── 场景启停
│   ├── 成本覆盖
│   └── 验证状态
├── 号池运营
│   ├── 账号导入/注册
│   ├── 登录态刷新
│   ├── 余额/签到
│   ├── 健康评分
│   ├── 冷却/禁用
│   └── 调度策略
├── 客户/API Key/配额
│   ├── API Key 生命周期
│   ├── 权限范围
│   ├── 配额
│   ├── 用量账单
│   └── 审计日志
├── 后台控制台
│   ├── 仪表盘
│   ├── 任务中心
│   ├── 账号池
│   ├── 模型场景
│   ├── API Keys
│   ├── 成本日志
│   ├── 上传素材
│   └── 系统设置
└── 运维安全
    ├── 健康检查
    ├── 指标
    ├── 日志
    ├── 备份
    ├── 密钥保护
    └── 部署配置
```

### 3.2 推荐数据流

```text
客户端
  -> POST /v1/uploads      可选，上传图片/视频素材
  -> POST /v1/generate     快速创建任务，返回 task_id
  -> 后台队列              task status: queued
  -> worker 选账号         检查账号健康/余额/模型能力/场景开关
  -> Oreate create_chat
  -> Oreate sse/stream
  -> Oreate getmessagelist 轮询水合
  -> 保存最终 assets/status/cost/error
  -> webhook 通知或客户端轮询 GET /v1/tasks/{id}
```

## 4. 功能规划

## 4.1 异步任务系统

### 现状

- 当前 `/v1/generate` 会同步提交并尝试水合结果。
- 视频任务可能需要较长轮询时间。
- 任务表有 `status`，但缺完整状态机和 worker。

### 目标

把生成请求改造成可运营的异步任务：

- 创建任务快。
- 后台 worker 执行。
- 支持查询、重试、取消、重新水合。
- 失败原因可追踪。
- 长视频不占用调用方 HTTP 请求。

### 状态机

```text
queued
  -> running
  -> submitted
  -> hydrating
  -> completed
  -> failed
  -> cancelled
  -> expired
```

状态说明：

- `queued`：任务已入库，等待 worker。
- `running`：worker 已领取，正在创建 chat 或提交 stream。
- `submitted`：上游已接受，但结果未出现。
- `hydrating`：正在轮询历史消息提取资源。
- `completed`：已拿到至少一个最终资源。
- `failed`：明确失败，记录错误码。
- `cancelled`：用户或管理员取消，本地不再继续轮询。
- `expired`：超过最大等待时间无结果。

### API

新增或调整：

- `POST /v1/generate`
  - 默认异步：返回 `task_id`、`status=queued/submitted`。
  - 可选 `sync_wait_seconds`，短等结果，默认不长等。
- `GET /v1/tasks`
  - 支持分页、状态筛选、时间筛选。
- `GET /v1/tasks/{task_id}`
  - 返回完整状态、assets、错误、成本、上游 ID。
- `POST /v1/tasks/{task_id}/retry`
  - 仅失败/过期任务可重试。
- `POST /v1/tasks/{task_id}/cancel`
  - 本地取消队列或停止后续水合。
- `POST /v1/tasks/{task_id}/hydrate`
  - 管理员或调用方触发重新水合。

### 后台按钮

任务中心每行需要：

- 查看详情。
- 复制 task_id。
- 复制资源 URL。
- 重新水合。
- 重试。
- 取消。
- 标记失败。

### 验收标准

- 创建视频任务不会阻塞到完整视频生成结束。
- worker 重启后能继续处理 `queued/submitted/hydrating` 任务。
- 同一任务不会被两个 worker 同时执行。
- 重试会生成新 attempt，保留旧 attempt 记录。
- 取消后不再继续轮询，但保留历史日志。

## 4.2 任务详情与结果管理

### 现状

后台任务表只展示少量字段，缺任务详情、资源预览和操作。

### 目标

任务中心必须能完成日常排障：

- 看清请求参数。
- 看清上游 payload。
- 看清 stream 事件摘要。
- 看清 hydration 响应摘要。
- 看清最终图片/视频。
- 看清错误码、账号、扣费风险。

### 后台页面

任务中心筛选：

- 状态。
- 类型：image/video。
- 模型。
- 场景。
- 账号。
- API Key/客户。
- 错误码。
- 时间范围。
- 是否有资产。

任务详情抽屉：

- 基础信息：task_id、kind、status、created_at、updated_at、request_id。
- 生成参数：prompt、model、ratio、resolution、duration、scene。
- 上传素材：附件列表、缩略图、视频时长。
- 上游信息：chatId、focusId、message_id、groupId。
- 结果：图片预览、视频播放器、下载链接。
- 错误：error_code、error_message、raw upstream error。
- 成本：估算点数、实际点数、账号余额变化。
- 尝试记录：attempt 列表。

### 数据建议

新增表：

```sql
task_attempts(
  id,
  task_id,
  account_id,
  status,
  error_code,
  error_message,
  request_payload_json,
  stream_summary_json,
  hydration_summary_json,
  assets_json,
  started_at,
  finished_at
)
```

`tasks` 表建议补字段：

- `api_key_id`
- `request_id`
- `status`
- `kind`
- `model_name`
- `scene_id`
- `resolution`
- `ratio`
- `duration`
- `estimated_point_cost`
- `actual_point_cost`
- `assets_json`
- `error_code`
- `error_message`
- `cancel_requested_at`

## 4.3 模型与视频场景治理

### 现状

- 已有 `/v1/capabilities`。
- 后台能刷新模型能力。
- 高级视频场景构造有单元测试，但缺真实成功样本。

### 目标

模型能力不只是“展示下拉”，还要成为运营开关和风险控制来源。

### 功能

模型列表：

- 模型名称。
- 类型：image/video。
- 描述。
- 支持分辨率、比例、时长、音频。
- 点数成本。
- 上游 aiType。
- 来源账号。
- 最后刷新时间。
- 验证状态：`unverified` / `unit_tested` / `live_verified` / `disabled`。

场景列表：

- `text_or_image`
- `reference`
- `frame_based`
- `motion`

每个场景需要：

- 是否对外开放。
- 是否允许后台手动测试。
- 上传槽位要求。
- 支持模型。
- 实测成功样本。
- 最近失败样本。
- 成本风险等级。

### 规则

- `text_or_image` 可开放，因已有真实文本生视频和上传图生视频成功样本。
- `reference`、`frame_based`、`motion` 默认关闭外部 API，只允许管理员实验。
- 未有真实成功样本的模型/场景组合不能进入默认调度。
- 能力刷新后如果模型成本、分辨率、时长发生变化，后台必须提示 diff。

### API

- `GET /v1/capabilities`
  - 保留。
  - 增加 `enabled`、`verification_status`、`risk_level`。
- `GET /v1/models`
  - 可选兼容接口，只返回模型基础列表，方便外部网关客户端接入。
- `PATCH /api/models/{model_id}/policy`
  - 启停、成本覆盖、风险等级。
- `PATCH /api/video-scenes/{scene_id}/policy`
  - 启停场景、限制客户、限制模型。

## 4.4 高级视频场景验证计划

### 目标

为 `reference`、`frame_based`、`motion` 建立真实成功证据，或者明确标记为不可用。

### 验证原则

- 每次验证前记录账号余额。
- 每次验证用最低成本模型和最低分辨率。
- 每次只验证一个变量。
- 每次保存完整请求、stream 摘要、history 摘要、CDN HEAD 结果。
- 如果出现 `100003` 且扣点，暂停该组合继续尝试。

### 验证矩阵

| 场景 | 最小输入 | 成功标准 | 默认开放 |
|---|---|---|---|
| `text_or_image` | prompt + 可选 image | 已有 MP4 CDN URL | 是 |
| `reference` | reference image/video | MP4 CDN URL，内容与参考相关 | 否 |
| `frame_based` | first_frame + last_frame | MP4 CDN URL，首尾帧生效 | 否 |
| `motion` | character_image + motion_video | MP4 CDN URL，动作迁移生效 | 否 |

### 后台实验室

新增“视频实验室”页面：

- 选择账号。
- 选择模型/场景。
- 上传素材。
- 自动展示预计点数。
- 提交前提示风险。
- 显示请求 JSON。
- 显示 stream/hydration 过程。
- 自动做 CDN HEAD 校验。
- 一键保存为成功样本。

## 4.5 上传素材管理

### 现状

- 已有 `/v1/uploads`。
- 后台生成页没有文件上传控件。

### 目标

上传能力要可测试、可复用、可排障。

### API

- `POST /v1/uploads`
  - 保留。
- `GET /v1/uploads`
  - 当前 API Key 上传记录。
- `GET /v1/uploads/{upload_id}`
  - 上传详情。
- `DELETE /v1/uploads/{upload_id}`
  - 本地记录删除；不承诺删除上游对象。

### 数据

新增 `uploads` 表：

- `id`
- `api_key_id`
- `account_id`
- `filename`
- `content_type`
- `size`
- `object_path`
- `bos_url`
- `message_attachment_json`
- `conversion_json`
- `created_at`

### 后台

上传管理页：

- 上传文件。
- 预览图片/视频。
- 复制 attachment JSON。
- 复制 message_attachment JSON。
- 查看用于哪些任务。

## 4.6 号池运营

### 现状

- 有账号导入、自动注册入口、冷却字段、失败计数。
- 账号选择会跳过冷却账号。
- 自动注册仍被 README 标记为未完成。
- 缺余额、签到、登录刷新、禁用、删除等运营动作。

### 目标

后台能判断“哪个账号能用、适合什么任务、还剩多少点、是否应被淘汰”。

### 账号状态

建议状态：

- `new`
- `verified`
- `active`
- `cooling`
- `low_balance`
- `invalid`
- `disabled`
- `banned`

### 健康维度

账号详情应包含：

- 登录健康：`isLogin`、`200001` 等。
- 生成健康：最近成功、最近失败、失败率。
- 风控健康：`212361` 次数。
- 参数失败：`200002` 次数。
- 服务失败：`100003` 次数和是否扣点。
- 余额：daily、bonus、restPoint。
- 能力缓存：图片/视频模型更新时间。
- 最近使用时间。
- 冷却到期时间。

### 调度策略

调度排序建议：

1. 状态必须可用。
2. 必须支持请求模型和场景。
3. 余额必须覆盖估算成本。
4. 不在冷却期。
5. 风险错误次数低。
6. 最近使用时间较早。
7. 成功率较高。

### 后台按钮

账号表：

- 查看详情。
- 刷新登录态。
- 刷新余额。
- 刷新模型能力。
- 手动签到。
- 启用。
- 禁用。
- 删除。
- 解除冷却。
- 设为冷却。
- 测试生图。
- 测试生视频。

### 自动维护

后台 worker 定时：

- 刷新余额。
- 尝试每日签到。
- 重登失效账号。
- 禁用连续失败账号。
- 当可用账号低于阈值时自动注册或提醒。

## 4.7 成本、配额与账单

### 现状

- API Key 有每分钟限流、每日请求、每日估算点数。
- usage_log 有估算点数字段。
- 缺实际扣点核算。

### 目标

能回答三个问题：

1. 谁用了多少？
2. 实际扣了多少？
3. 哪些失败也扣了点？

### 功能

- 请求前记录账号点数快照。
- 请求后记录账号点数快照。
- 计算实际点数变化。
- 支持 daily/bonus 分账。
- 对 `100003` 等失败但扣点情况标红。
- 每 API Key 日报。
- 每模型成本统计。
- 每账号消耗统计。

### 报表

后台成本页：

- 今日请求数。
- 今日成功率。
- 今日估算点数。
- 今日实际点数。
- 失败扣点次数。
- 按 API Key 分组。
- 按模型分组。
- 按账号分组。

## 4.8 API Key 与客户治理

### 现状

- 有 API Key 创建、删除、限流和配额。
- 删除 Key 会删除用量日志，不适合生产审计。
- 缺客户、权限范围、过期时间、轮换。

### 目标

API Key 不是单纯字符串，而是客户访问策略。

### 数据

新增 `clients`：

- `id`
- `name`
- `contact`
- `status`
- `created_at`

扩展 `api_keys`：

- `client_id`
- `expires_at`
- `allowed_kinds`
- `allowed_models`
- `allowed_scenes`
- `allow_uploads`
- `allow_experimental_scenes`
- `disabled_reason`
- `rotated_from_key_id`

### 后台按钮

API Key 页面：

- 创建 Key。
- 复制新 Key。
- 启用/停用。
- 轮换。
- 设置过期时间。
- 设置允许类型：image/video/upload。
- 设置模型白名单。
- 设置场景白名单。
- 查看该 Key 用量。

### 审计要求

- 删除 Key 只能软删除或禁用。
- 用量日志不能因删除 Key 被删除。
- 所有 Key 策略变更写入 admin audit log。

## 4.9 后台控制台

### 总体要求

当前后台是基础管理页。生产级后台要从“能点按钮”升级为“可运营、可排障、可控风险”。

### 页面规划

#### 仪表盘

展示：

- 可用账号数。
- 冷却账号数。
- 今日任务数。
- 今日成功率。
- 今日估算点数。
- 今日实际扣点。
- 当前队列长度。
- 最近错误码分布。

#### 任务中心

见 4.2。

#### 号池管理

见 4.6。

#### 模型与场景

见 4.3。

#### 视频实验室

见 4.4。

#### 上传素材

见 4.5。

#### API Keys / 客户

见 4.8。

#### 用量与成本

见 4.7。

#### 系统设置

应拆分：

- 管理员账号。
- Oreate 上游。
- 网关默认策略。
- worker 策略。
- 号池策略。
- 邮箱/注册策略。
- 安全策略。
- 备份策略。

## 4.10 安全

### 现状风险

- 账号密码、OUID、ouss 存储在 SQLite。
- 管理员 token 是内存态，缺过期时间。
- 注册验证链路有 `verify=False`。
- 后台使用 localStorage 存 admin token。

### 目标

单实例生产至少做到：

- 账号敏感字段加密存储。
- 管理员 token 有过期时间。
- 支持强制登出所有会话。
- TLS 校验默认开启。
- 配置文件不暴露密钥。
- 后台操作有审计日志。
- API Key 不可再次明文查看，只在创建时展示一次。

### 功能

- 增加 `ENCRYPTION_KEY` 或配置项。
- 迁移账号密码、OUID、ouss 为加密字段。
- `admin_sessions` 表持久化管理后台会话。
- 增加 admin audit log。
- 删除或配置化 `verify=False`。
- 增加敏感日志脱敏。

## 4.11 运维与部署

### 必需接口

- `GET /healthz`
  - 进程存活。
- `GET /readyz`
  - DB 可写、配置可读、至少一个可用账号。
- `GET /metrics`
  - 任务数、成功率、错误码、队列长度、账号状态。

### 日志

至少分三类：

- request log。
- task event log。
- admin audit log。

### 备份

- SQLite 定时备份。
- `config.json` 备份。
- 恢复演练文档。

### 部署

- 提供 systemd 或 Windows service 运行说明。
- 提供 `.env`/配置模板。
- 提供启动前检查命令。
- 提供升级迁移说明。

## 5. 开发阶段

## Phase 0：风险收口与开关

目标：避免未验证高级能力被误开放。

任务：

- 增加模型/场景启停配置。
- 默认关闭 `reference`、`frame_based`、`motion` 外部 API。
- 后台显示“实验性”标记。
- 禁止删除 API Key 时删除 usage_log。
- 配置化 TLS verify。

验收：

- 外部 API 调用未启用高级场景返回明确错误。
- 管理员可在后台看到哪些场景未验证。
- 删除/禁用 Key 不影响历史用量。

## Phase 1：异步任务与任务中心

目标：让视频任务具备生产级生命周期。

任务：

- 增加队列表和 attempt 表。
- `POST /v1/generate` 支持异步创建。
- 增加 worker 循环。
- 增加任务详情 API。
- 增加 retry/cancel/hydrate API。
- 后台任务中心增加详情抽屉和操作按钮。

验收：

- 长视频不阻塞 HTTP 请求。
- worker 重启后可恢复任务。
- 后台能重试失败任务。
- 后台能重新水合已提交任务。

### 实施记录（2026-07-09）

- Phase 0 已关闭并通过回归：`reference`、`frame_based`、`motion` 默认关闭，API Key 删除保持软删除，TLS verify 配置化，自动注册/后台接口已脱敏，不再返回明文密码、token。
- Phase 1 具备基础骨架：`POST /v1/generate` 默认异步排队，已存在任务状态机、attempt 记录、worker、`/v1/tasks/{id}`、retry/cancel/hydrate 和后台任务中心。
- Phase 2 已落地最小切片：账号余额快照、后台刷新余额、列表安全展示、低余额调度跳过。
- 下一步继续补 Phase 2 剩余运营面：成本对账的最小闭环，以及 API Key 客户治理的最小骨架。
- 测试约束：凡是会修改 `server.CFG` 的用例，必须先深拷贝再做 `deep_merge`，避免 scene/model policy 修改回流到共享嵌套字典，导致跨测试泄漏。
- 当前 P4 最小切片优先做 admin session 过期 + revoke/logout + admin audit log，补上后台最小安全闭环；备份/恢复继续放在后续最小增量。
- 上述 P4 最小切片已落地并回归通过：`admin_sessions` 表、session TTL、`/api/admin/logout`、`/api/admin/audit-logs`、后台审计面板、以及 admin 请求审计中间件已完成；下一段仅剩备份/恢复与更细的会话治理收口。
- 备份/恢复最小切片将仅提供本地 SQLite + `config.json` 打包下载和管理员手动恢复，不引入定时调度、对象存储或跨机复制。
- 备份/恢复最小切片已落地并回归通过：`/api/admin/backup`、`/api/admin/restore`、后台按钮、以及 zip 里的 `accounts.db` / `config.json` / `manifest.json` 已验证；当前 P4 仅剩更细的会话治理收口与恢复演练文档补充。

## Phase 2：号池健康与余额

目标：账号池可运营。

任务：

- 接入账号余额/点数详情。
- 保存余额快照。
- 增加账号健康字段。
- 后台账号详情页。
- 手动重登/刷新余额/刷新能力/禁用/启用/解除冷却。
- 调度前检查余额覆盖成本。
- 定时维护任务。

本轮优先最小切片：

- 仅落地余额快照字段、后台刷新余额接口、`/api/accounts` 安全展示、调度前低余额跳过。
- 先不扩展到完整健康分、风控矩阵、定时维护编排。

已完成后，后续最小增量改为：

- 给账号列表补 derived `health_status` / `risk_status` / 冷却剩余时间。
- 给 `/v1/accounts/status` 补池内健康统计，便于运营看板直接使用。

当前下一段优先级：

- 给任务记录和用量日志补前后余额快照与实际点数。
- 给 API Key 增加客户归属的最小骨架，先能创建/查看客户并给 Key 绑定客户。
- 保留现有账号健康字段，不做更大范围的池治理重构。

下一阶段最小切片：

- `healthz` / `readyz` / `metrics`
- 先把部署系统能直接用的健康信号补齐，不扩到备份、审计和会话过期的完整方案。

再下一阶段最小切片：

- 模型/场景 policy 的后台 patch 路由
- 先补 `PATCH /api/models/{model_id}/policy` 和 `PATCH /api/video-scenes/{scene_id}/policy`，把现有默认关闭/实验性开关真正做成后台可写控制。

验收：

- 后台能看到每个账号余额。
- 低余额账号不会被高成本任务选中。
- 连续失败账号自动冷却或禁用。

## Phase 3：模型治理与高级视频验证

目标：把能力发现变成能力治理。

任务：

- 增加模型/场景策略表。
- 增加能力刷新 diff。
- 增加成本覆盖。
- 增加视频实验室。
- 逐个验证 `reference`、`frame_based`、`motion`。
- 保存成功样本。

验收：

- 每个对外开放场景都有真实成功样本。
- 高级场景未验证前不能被外部 Key 调用。
- 模型成本变化会被后台提示。

## Phase 4：客户、API Key、成本账单

目标：支持多调用方受控使用。

任务：

- 增加 clients。
- API Key 增加权限范围、过期时间、轮换。
- 记录实际扣点。
- 增加成本报表。
- 增加失败扣点告警。

验收：

- 不同客户用量可分开统计。
- Key 可限制只用图片或只用指定视频场景。
- 实际扣点和估算点数都能查。

## Phase 5：安全、运维、发布

目标：达到单实例生产发布标准。

任务：

- admin session 过期和 revoke/logout。
- admin audit log 落库与查询。
- 敏感字段加密。
- healthz/readyz/metrics。
- 备份与恢复实现与文档。
- 部署文档。
- 压测与故障演练。

验收：

- 密钥和账号敏感字段不以明文暴露在 API/后台。
- 健康检查能被部署系统使用。
- 备份可恢复。
- 全量测试通过。

## 6. 横向依赖

| 上游 | 下游 | 说明 |
|---|---|---|
| 场景启停 | 高级视频开放 | 未验证场景必须先有开关 |
| 异步任务 | 后台任务中心 | 没有状态机就无法做任务操作 |
| 账号余额 | 成本账单 | 实际扣点需要前后余额快照 |
| API Key 客户 | 成本报表 | 报表需要客户归属 |
| 模型策略 | 调度策略 | 调度必须知道模型/场景是否可用 |
| admin audit | 安全发布 | 生产后台操作必须留痕 |

## 7. 测试计划

### 单元测试

- 参数校验。
- 状态机流转。
- 调度策略。
- API Key 权限。
- 配额计算。
- 成本计算。
- 敏感字段脱敏。

### 集成测试

- 创建任务 -> worker 执行 -> completed。
- ping-only 视频 -> hydrating -> completed。
- failed -> retry -> completed。
- cancelled 不再继续执行。
- 上传素材 -> 视频生成 payload。
- Key 权限拒绝高级场景。

### 后台 HTML/JS 测试

- 页面包含关键按钮。
- JS 语法解析。
- 模型/场景下拉联动。
- 任务详情渲染。
- API Key 策略提交。

### 真实账号验证

必须人工确认后执行：

- 生图低成本样本。
- 文本生视频低成本样本。
- 上传图生视频 `text_or_image` 回归样本。
- `reference` 最低成本样本。
- `frame_based` 最低成本样本。
- `motion` 最低成本样本。

每次真实验证必须记录：

- 日期。
- 账号 ID。
- 模型。
- 场景。
- 请求参数。
- 预估成本。
- 前后余额。
- chatId。
- 最终资产 URL。
- CDN HEAD 结果。
- 是否扣点。

## 8. 发布门槛

### 内测门槛

- Phase 0 完成。
- Phase 1 完成。
- 图片、文本视频、`text_or_image` 上传视频保持可用。
- 后台能查看任务详情并重试。

### 受控生产门槛

- Phase 2 完成。
- Phase 3 至少完成场景开关和未验证场景禁用。
- Phase 4 至少完成 Key 权限范围和成本统计。
- Phase 5 至少完成 healthz/readyz、备份、admin session 过期。
- Phase 5 已补齐 healthz/readyz、备份/恢复、admin session 过期与审计；剩余是更细的恢复演练和敏感字段加密。

### 完整生产门槛

- 所有对外开放视频场景都有真实成功样本。
- 账号池自动维护可用。
- 实际扣点账单可用。
- 安全审计通过。
- 全量测试、编译、JS 解析、敏感信息扫描通过。

## 9. 风险矩阵

| 风险 | 影响 | 缓解 |
|---|---|---|
| Oreate 上游协议变化 | 生成失败或扣点 | 能力 diff、真实验证、错误分类 |
| 高级视频 `100003` 扣点 | 成本损失 | 默认关闭、实验室低成本验证 |
| 账号风控 `212361` | 账号不可用 | 健康评分、冷却、禁用 |
| 余额不足 | 任务失败 | 调度前余额检查 |
| 同步视频请求超时 | 客户体验差 | 异步任务 + webhook |
| 删除 API Key 丢审计 | 无法追账 | 软删除，保留 usage_log |
| 明文账号凭据泄漏 | 安全事故 | 加密存储、脱敏、备份保护 |
| 后台误操作 | 生产事故 | admin audit、确认弹窗、危险操作二次确认 |

## 10. 实施纪律

1. 每个 Phase 先补文档和测试，再写实现。
2. 每个 Phase 完成后更新 README 和本计划状态。
3. 真实账号验证必须先获得用户明确同意。
4. 高级视频场景默认保持关闭，直到有真实成功样本。
5. 涉及账号、Key、cookie、token 的 diff 必须做敏感信息扫描。
6. 生产发布前必须跑：

```bash
python -m unittest discover -s tests -p "*_tests.py"
python -m py_compile server.py banti_token_generator.py
node -e "const fs=require('fs'); const text=fs.readFileSync('server.py','utf8'); const html=text.match(/ADMIN_HTML = \"\"\"([\\s\\S]*?)\"\"\"/)[1]; const script=html.match(/<script>([\\s\\S]*?)<\\/script>/)[1]; new Function(script); console.log('js parse ok');"
git diff --check
```

## 11. 推荐下一步

建议下一轮先做 Phase 2 最小切片：

1. 先补账号余额快照和后台刷新余额接口。
2. 让 `/api/accounts` 和后台号池展示安全余额。
3. 调度前跳过明显低于 `estimated_point_cost` 的账号。
4. 再继续扩展健康、风控、冷却和对账闭环。

理由：

- 这一步直接降低误扣点风险。
- 这一步解决视频网关最核心的长任务问题。
- 后续号池、成本、模型治理都依赖更完整的任务状态和事件记录。
