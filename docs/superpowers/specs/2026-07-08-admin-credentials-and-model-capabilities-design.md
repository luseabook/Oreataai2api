# 管理凭据与模型能力目录设计

## 目标

把当前后台的管理员账号密码修改从通用设置中拆出来，形成独立、安全的凭据修改流程；同时补齐生图/生视频网关最基本的能力发现接口，让调用方和后台页面都能获取模型描述、支持分辨率、比例、时长和场景。

## 背景问题

当前项目已经可以用账号池提交图片/视频生成，但作为网关还不完整：

- 管理员密码只能混在通用设置里修改，缺少当前密码校验，修改后旧登录 token 也不会失效。
- `/v1/generate` 只有生成入口，没有 `/v1/capabilities` 这类模型能力发现接口。
- 视频模型配置里已有 `description`、`duration`、`videoResolution`、`videoSize`、`scenes`，但没有规范化暴露给调用方和后台页面。
- 后台生成表单仍是自由输入模型/分辨率/比例/场景，用户不知道可选项。

## 方案

### 1. 管理员凭据修改

新增后台接口：

`POST /api/admin/credentials`

请求体：

```json
{
  "current_password": "old password",
  "new_username": "admin",
  "new_password": "new strong password",
  "confirm_password": "new strong password"
}
```

行为：

- 需要管理员 Bearer token。
- 必须验证 `current_password` 与当前配置一致。
- `new_username` 必须非空。
- `new_password` 与 `confirm_password` 必须一致。
- 新密码拒绝占位密码：空值、`admin123`、`CHANGE_ME`、`changeme`、`password`。
- 新密码最短 8 位。
- 修改成功后写入 `config.json`。
- 修改成功后清空 `ADMIN_TOKENS`，强制重新登录。
- `/api/admin/settings` 必须忽略 `server.admin_username` 和 `server.admin_password`，避免绕过当前密码校验。

后台 UI：

- 设置页新增“管理员账号”区块。
- 字段：当前密码、新用户名、新密码、确认新密码。
- 成功后清空本地 `localStorage` token，并回到登录面板。
- 通用设置里的“管理员密码”输入移除，避免和普通系统配置混用。
- 修改成功后旧 token 立即失效，页面回到登录面板。

### 2. 模型能力目录

新增网关接口：

`GET /v1/capabilities`

认证：

- 使用现有 API Key：`Authorization: Bearer <API Key>`。

响应结构：

```json
{
  "ok": true,
  "source_account_id": 1,
  "image": {
    "models": [
      {
        "name": "Google Nano Banana 2",
        "description": "Flagship 4K high-resolution",
        "icon": "https://...",
        "resolutions": ["4K", "2K", "1K"],
        "ratios": ["16:9", "1:1"],
        "point_cost": [{"resolution": "4K", "point": 12}]
      }
    ]
  },
  "video": {
    "models": [
      {
        "name": "Seedance 2.0 Mini",
        "description": "localized description",
        "icon": "https://...",
        "durations": [5, 10],
        "resolutions": ["480", "720"],
        "ratios": ["16:9", "9:16"],
        "supports_audio": true,
        "supports_modify_size": true,
        "point_cost_image": [],
        "point_cost_reference": []
      }
    ],
    "scenes": [
      {
        "scene_id": "text_or_image",
        "name": "Text or image to video",
        "description": "localized description",
        "icon": "https://..."
      }
    ]
  }
}
```

新增后台接口：

- `GET /api/models/capabilities`：管理员查看当前规范化能力目录。
- `POST /api/models/refresh`：选择一个可用账号，实时请求 Oreate 图片模型、视频模型、视频场景接口，并刷新该账号缓存。

数据来源：

- 优先读取账号表里的 `model_info_json` 和 `video_info_json`。
- 如果缓存缺失，后台刷新接口使用 `CLIENT.fetch_image_models()`、`CLIENT.fetch_video_models()`、`CLIENT.fetch_video_scenes()` 重新拉取。
- 不在本轮新增独立模型表，避免迁移面过大；后续如果需要跨账号聚合、缓存过期和版本审计，再独立建表。

规范化规则：

- 多语言字段优先取 `zh`，再取 `en`，再取第一个字符串值。
- 图片模型：
  - `name` 从 `modelName` 取。
  - `description` 从 `modelDesc` 取。
  - `resolutions` 从 `resolution` 取。
  - `ratios` 从 `size[].ratio` 取。
  - `point_cost` 从 `pointCost` 取。
- 视频模型：
  - `name` 从 `modelName` 取。
  - `description` 从 `description` 多语言对象取。
  - `durations` 从 `duration` 取。
  - `resolutions` 从 `videoResolution` 取。
  - `ratios` 从 `videoSize[].ratio` 或字符串列表取。
  - `supports_audio` 从 `supportAudio` 取。
  - `supports_modify_size` 从 `supportModifySize` 取。
- 视频场景：
  - `scene_id` 从 `sceneId` 取。
  - `name` 从 `sceneName` 多语言对象取。
  - `description` 从 `description` 多语言对象取。

### 3. 后台生成表单

后台页面在初始化后调用 `GET /api/models/capabilities`：

- 图片模式：模型、分辨率、比例使用图片能力列表填充。
- 视频模式：模型、分辨率、比例、时长、场景使用视频能力列表填充。
- 模型切换后，联动更新该模型支持的分辨率、比例、时长。
- 如果能力目录为空，保留自由输入能力，并展示接口返回错误。

### 4. 参数校验边界

本轮只做基本校验：

- `kind` 必须是 `image` 或 `video`。
- 如果用户传了 `model_name/resolution/ratio/duration/scene_id`，生成接口按传入值转发。
- 后台表单尽量通过下拉减少错误参数。

不在本轮做强制参数白名单拦截，原因是 Oreate 侧模型配置可能变化较快，网关先保证能力发现和可选项展示，后续再加入严格校验和错误提示。

## 测试要求

- 管理员凭据接口：
  - 未登录返回 401。
  - 当前密码错误返回 401。
  - 两次新密码不一致返回 400。
  - 占位或过短密码返回 400。
  - 修改成功写入配置并清空旧 token。
  - 通用设置接口不能修改管理员用户名或密码。
- 模型能力目录：
  - 能从样例图片配置中规范化模型描述、分辨率、比例。
  - 能从样例视频配置中规范化模型描述、分辨率、比例、时长、场景。
  - `/v1/capabilities` 需要 API Key。
  - `/api/models/capabilities` 需要管理员 token。
- 后台 HTML：
  - 包含凭据修改表单。
  - 能调用模型能力接口。
  - 不再用普通设置表单直接提交管理员密码。

## 后续优化

本轮之后，作为生产级生图/生视频网关仍建议继续做：

- 账号池调度：按余额、模型能力、失败率、冷却时间选择账号，而不是简单取最新账号。
- 任务生命周期：增加结果轮询/SSE、状态刷新、失败重试和取消。
- 参数白名单校验：基于能力目录拒绝无效模型/分辨率/比例。
- 统一错误格式：让 `/v1/*` 返回稳定的错误码和可读 message。
- 速率限制和配额：按 API Key 保护成本。
- 请求幂等：支持 `Idempotency-Key` 防止重复扣费。
- 日志与审计：记录调用方、模型、成本、账号、失败原因。
- TLS 验证：移除或配置化 `verify=False`。
