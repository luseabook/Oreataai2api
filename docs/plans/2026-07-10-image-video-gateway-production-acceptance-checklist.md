# 生图生视频网关上线硬验收清单

## 1. 文档目的

本文用于解决一个明确问题：以后不能再用“功能做了很多”来判断项目是否已经是合格网关。

从本文开始，项目状态只按硬验收口径判断：

- 只要 P0/P1 有任意一项未绿，只能叫“受控内测可用”。
- 只有所有“生产必过项”都有证据，才能叫“合格生产网关”。
- 未验证高级视频能力不能靠代码推断放行，必须有真实成功样本。

本文是上线口径文件，不是实现计划。实现时可以拆阶段，但验收时必须回到本文逐项打勾。

## 2. 状态分级

### S0：原型

能跑通少量生图或生视频请求，但任务、账号、成本、安全、后台不闭环。

### S1：受控内测可用

适合自用、小流量、人工盯着跑。

最低条件：

- 生图、基础文本生视频、已验证上传图生视频可用。
- 有 API Key 和基础限流/配额。
- 有异步任务、查询、重试、取消、重新水合。
- 后台能看账号、任务、模型、Key、用量。
- 敏感信息不从 API/后台响应直接泄漏。
- 失败扣点可记录。
- `healthz` / `readyz` / `metrics` 可用。
- 备份/恢复可手动执行。

当前项目按已完成内容判断：接近或达到 S1。

### S2：合格生产网关

适合对外给客户使用和收费，但仍以单实例或小规模部署为边界。

最低条件：

- 所有对外开放能力都有真实验证证据。
- API Key 可以精细限制客户可用能力。
- 账号敏感字段加密存储。
- 成本、任务、客户、Key 能对账。
- 后台能高效筛选、定位和处理故障。
- 备份恢复、升级、事故处理有演练文档。

当前项目还不能直接标记为 S2。

### S3：规模化生产网关

多实例、高并发、多租户账单、自动扩缩容、告警体系、SLA 和持续风控完整。本文暂不要求达到 S3。

## 3. 一票否决项

出现以下任意一项，不允许称为“合格生产网关”：

1. 任意对外开放的视频场景没有真实成功样本。
2. 数据库或备份中明文保存账号密码、`OUID`、`ouss`，且没有加密保护方案。
3. API Key 无法限制模型、场景、上传、分辨率或时长。
4. 成本无法按客户/API Key/账号/模型/任务状态追溯。
5. 失败但扣点的任务无法被记录和查询。
6. 后台无法按任务状态、客户、Key、账号、模型、错误码筛选。
7. 备份恢复会恢复旧后台 session 或缺少恢复后检查流程。
8. 真实账号验证需要消耗额度但没有人工确认。
9. 配置、日志、响应或提交中出现真实 cookie、token、账号密码、API Key。

## 4. 生产必过项

### P0：安全与资金风险

| ID | 验收项 | 合格标准 | 当前判断 | 必须证据 |
|---|---|---|---|---|
| P0-01 | 响应脱敏 | 注册、账号、后台接口不返回密码、邮箱 token、Oreate cookie、API Key 明文二次展示 | 已基本完成 | 脱敏回归测试、敏感信息扫描 |
| P0-02 | 敏感字段加密 | 账号密码、`OUID`、`ouss`、长期 token 不以明文存库或明文进入备份 | 已完成 | 加密迁移测试、备份内容抽检 |
| P0-03 | 失败扣点对账 | `100003` 等失败但扣点任务记录 `actual_point_cost`、前后余额、错误码 | 已基本完成 | 失败扣点单测、后台可查 |
| P0-04 | API Key 软删除 | 删除/禁用 Key 不删除历史用量 | 已完成 | 回归测试 |
| P0-05 | 管理会话安全 | session 过期、logout、强制 revoke、恢复备份后旧 session 失效 | 已基本完成 | session 回归测试 |
| P0-06 | 真实验证授权 | 任何消耗真实额度的验证必须先获得人工确认 | 流程要求已明确 | 验证记录中保留确认说明 |

### P1：核心网关能力

| ID | 验收项 | 合格标准 | 当前判断 | 必须证据 |
|---|---|---|---|---|
| P1-01 | 能力发现 | 调用方可查询图片/视频模型、场景、分辨率、时长、成本、启停状态 | 已基本完成 | `/v1/capabilities` 测试 |
| P1-02 | 生图任务 | 最低成本生图真实成功，返回可访问图片资产 | 已完成过 | README 或验证记录 |
| P1-03 | 文本生视频 | 最低成本文本视频真实成功，返回可访问 MP4 | 已完成过 | README 或验证记录 |
| P1-04 | 上传图生视频 | `text_or_image` 上传类视频真实成功 | 已完成过 | 真实验证记录 |
| P1-05 | 异步任务生命周期 | queued/running/submitted/hydrating/completed/failed/cancelled/expired 状态可追踪 | 已基本完成 | 状态机测试 |
| P1-06 | 取消任务 | 取消后不继续水合、不继续回写成功结果 | 需复核 | 取消中的 worker 测试 |
| P1-07 | 卡住任务回收 | submitted/hydrating 有超时、退避、过期和可重水合边界 | 已部分完成 | 过期/重水合测试 |

### P2：高级视频治理

| ID | 验收项 | 合格标准 | 当前判断 | 必须证据 |
|---|---|---|---|---|
| P2-01 | 高级场景默认关闭 | `reference`、`frame_based`、`motion` 未验证前不能被普通外部 Key 调用 | 已完成 | 策略测试 |
| P2-02 | reference 真实验证 | 最低成本 reference 场景产出 MP4，前后余额和请求记录完整 | 已完成 | `live_validation/advanced-video-validation-20260711-235634.json` |
| P2-03 | frame_based 真实验证 | 首尾帧场景产出 MP4，首尾帧语义有效 | 已完成 | `live_validation/advanced-video-validation-20260712-001820-retry.json` |
| P2-04 | motion 真实验证 | 动作迁移场景产出 MP4，动作语义有效 | 未完成：真实提交仍被上游拒绝 | `live_validation/advanced-video-validation-20260712-002744.json` / `20260712-003426.json` / `20260712-003622.json` |
| P2-05 | 视频实验室 | 管理员可选择账号、模型、场景、上传素材、预估成本并保存验证样本 | 未完成 | 后台功能和测试 |
| P2-06 | 验证样本库 | 每个开放组合都有日期、账号、模型、场景、请求、余额、chatId、资产 URL、CDN HEAD | 未完成 | 样本记录 |

### P3：客户与 API Key 治理

| ID | 验收项 | 合格标准 | 当前判断 | 必须证据 |
|---|---|---|---|---|
| P3-01 | 客户归属 | 每个 API Key 可绑定客户，任务和用量能回查客户 | 已有骨架 | 客户/Key 测试 |
| P3-02 | 权限范围 | Key 可限制 kind、model、scene、upload、experimental、resolution、duration | 已完成 | 权限拒绝测试 |
| P3-03 | 过期与轮换 | Key 支持过期时间、禁用原因、轮换来源 | 已完成 | Key 生命周期测试 |
| P3-04 | 配额 | 每分钟、每日请求、每日点数限制稳定生效 | 已基本完成 | 配额测试 |
| P3-05 | Key 明文规则 | 新 Key 只创建时展示一次，之后只能显示摘要 | 已完成 | 后台/API 测试 |

### P4：账号池与调度

| ID | 验收项 | 合格标准 | 当前判断 | 必须证据 |
|---|---|---|---|---|
| P4-01 | 余额快照 | 请求前后记录账号余额，支持 actual cost | 已基本完成 | 成本测试 |
| P4-02 | 低余额跳过 | 账号余额不足时不被高成本任务选中 | 已基本完成 | 调度测试 |
| P4-03 | 健康摘要 | 后台和状态接口展示健康、冷却、风控、余额更新时间 | 已基本完成 | 接口测试 |
| P4-04 | 自动维护 | 定时刷新余额、签到、重登、冷却解除、失效禁用 | 未完成 | worker/维护测试 |
| P4-05 | 调度策略 | 按状态、能力、余额、冷却、失败率、最近使用排序 | 需复核 | 调度排序测试 |

### P5：后台运营

| ID | 验收项 | 合格标准 | 当前判断 | 必须证据 |
|---|---|---|---|---|
| P5-01 | 任务分页筛选 | 后台任务支持分页、状态、类型、模型、场景、账号、Key、客户、错误码、时间筛选 | 已完成 | 后台/API 测试 |
| P5-02 | 任务详情 | 展示请求参数、上游摘要、attempt、资产预览、成本、错误 | 已部分完成 | 后台测试 |
| P5-03 | 用量分页筛选 | 用量支持分页、客户、Key、账号、模型、状态、日期筛选 | 已完成 | 后台/API 测试 |
| P5-04 | 成本报表 | 按客户、Key、账号、模型、日期、成功/失败扣点聚合 | 已完成 | 报表测试 |
| P5-05 | 上传素材管理 | 后台可上传、预览、复制 attachment、查看关联任务 | 已完成 | 后台测试 |
| P5-06 | 模型策略后台 | 后台可启停模型/场景、设置风险和成本覆盖 | 已部分完成 | policy patch 测试 |

### P6：运维发布

| ID | 验收项 | 合格标准 | 当前判断 | 必须证据 |
|---|---|---|---|---|
| P6-01 | 健康检查 | `healthz` 存活、`readyz` 只在可调度账号存在时 200 | 已完成 | readyz 测试 |
| P6-02 | metrics | 输出任务、错误码、队列、账号状态、今日用量关键指标 | 已基本完成 | metrics 测试 |
| P6-03 | 备份恢复 | 可备份 DB/config，可恢复，恢复后旧 session 失效 | 已基本完成 | 备份恢复测试 |
| P6-04 | 恢复演练文档 | 有恢复步骤、恢复后检查、失败回滚、密钥注意事项 | 已完成 | runbook 文档 |
| P6-05 | 部署文档 | 有启动、升级、迁移、环境变量、systemd/Windows service 指南 | 已完成 | 部署文档 |
| P6-06 | 发布前检查 | 单测、编译、后台 JS、diff check、敏感信息扫描固定为发布命令 | 已完成 | 发布清单 |

## 5. 当前项目差距汇总

按本文口径，当前项目主要差在：

1. 高级视频三个场景的真实成功验证和样本库。
2. 自动账号维护。
3. 视频实验室和验证样本库。

这些不是“修 bug”级别的小项，而是生产网关的验收门槛。

## 6. 下一轮推荐顺序

为了最快从 S1 推进到 S2，建议按下面顺序：

1. P2-05 / P2-06：视频实验室和验证样本库。
2. P2-02 / P2-03 / P2-04：在人工确认后做真实高级视频验证。
3. P4-04：自动账号维护。

原因：

- 视频实验室和样本库是高级场景真实验证前的必要运营边界。
- 视频实验室先于真实验证，可以避免盲目消耗额度。
- 自动账号维护决定长期可用性，但不应阻塞人工批准后的高级场景验证。

## 6.1 2026-07-10 实施更新

本轮已按测试先行方式关闭 3 个高优先验收项：

- `P0-02`：账号密码、`OUID`、`ouss` 已加密存储；旧明文字段可迁移；备份中的 `accounts.db` 不再包含这些明文。
- `P3-02`：API Key 已支持 `kind/model/scene/upload/experimental/resolution/duration` 细粒度限制，并在网关请求上拒绝越权调用。
- `P5-04`：后台已补成本报表接口和表格，支持按日期、客户、Key、账号、模型聚合，并拆出成功/失败扣点。

截至 2026-07-10 本轮完成后，项目仍然不是 `S2`，主因不是安全和 Key 边界，而是：

- `P2-02` / `P2-03` / `P2-04` 仍缺真实高级视频成功样本。
- `P5-01` / `P5-03` 当时仍缺任务/用量分页筛选，已在 2026-07-11 收口。
- `P6-04` / `P6-05` 当时仍缺恢复演练与部署文档，已在 2026-07-11 收口。

## 6.2 2026-07-11 S2 自动化收口更新

本轮继续按测试先行方式关闭以下自动化验收项：

- `P3-03` / `P3-05`：API Key 已支持 `expires_at`、`disabled_reason`、`rotated_from_id`、`rotation_note`；过期 Key 会被网关和 OpenAI-compatible 路由拒绝；明文 Key 只在创建响应返回一次。
- `P5-01` / `P5-03`：后台任务和用量列表已支持分页、日期、状态、类型、模型、场景、账号、API Key、客户和错误码筛选，并返回账号邮箱、Key 名称、客户名称。
- `P5-05`：新增 `/api/admin/uploads` 上传素材列表，支持筛选、脱敏 attachment、复制 object/attachment、查看关联任务数，并在后台页面展示。
- `P6-04` / `P6-05` / `P6-06`：新增部署、备份恢复、发布检查 runbook，并用文档测试固定关键上线命令和边界。

截至 2026-07-11，本轮完成后，项目对已验证的生图、文本生视频和 `text_or_image` 上传图生视频能力已具备 `S2-ready` 的自动化运营边界；但仍不能标记为完整 `S2`，因为：

- `P2-02` / `P2-03` / `P2-04` 仍缺真实高级视频成功样本。
- `P2-05` / `P2-06` 仍缺视频实验室和验证样本库。
- `P4-04` 自动账号维护仍未完成。

## 6.3 2026-07-12 高级视频真实验证更新

用户已明确授权真实额度消耗。本轮用 `live_validation/run_advanced_video_live_validation.py` 在进程内临时开启高级场景策略，未修改 `config.json`，证据 JSON 已脱敏，不持久化 API Key、Cookie、`jt` 或原始上游请求镜像。

- `P2-02 reference`：已完成。证据 `live_validation/advanced-video-validation-20260711-235634.json`，任务 `7`，模型 `Seedance 2.0 Mini`，分辨率 `480`，产物 `https://cdn.oreateai.com/aivideo/videodownload/2698213035.mp4`，预览帧 `live_validation/advanced-video-validation-20260711-235634/reference-1-preview.jpg`。
- `P2-03 frame_based`：已完成。证据 `live_validation/advanced-video-validation-20260712-001820-retry.json`，任务 `10`，模型 `Seedance 1.5 Pro`，分辨率 `480`，产物 `https://cdn.oreateai.com/aivideo/videodownload/2543040596.mp4`，预览帧 `live_validation/advanced-video-validation-20260712-001820/frame_based_seedance_15_pro-1-preview.jpg`。
- `P2-04 motion`：仍未完成。已修正视频上传不应调用 `/oreate/convert/submit`、视频附件补 `videoDurationSec/videoWidth/videoHeight`、motion 场景按当前前端限制不发送 `duration` 和 `ratio`。真实任务 `12` / `13` / `14` 均为 `Kling 2.6`，合规 3 秒 512x512 motion 素材仍返回 `100003 call service error`；任务 `15` 用 `Kling 3.0` + 前端默认 `keepOriginalSound=true` 返回 `200017 point exceed`。实时余额探测显示账号池 `2` 到 `25` 均为 `0` 点，无法继续完成高成本 motion 验证。

Guardrail: Do not register or rotate into new upstream accounts to bypass HTTP 403, quota exhaustion, or risk controls. Only rerun `P2-04 motion` with a legitimately provisioned upstream account that has sufficient points, and keep `P2-04` incomplete until the evidence file contains a completed task with a reachable MP4 asset. A 2026-07-12 follow-up balance probe reconfirmed accounts `2` through `25` at `rest_point=0`, `daily_point=0`, and `bonus_point=0`, so no `motion` generation was started.

## 7. 验收执行规则

每次新窗口实现必须遵守：

1. 先选择本文中的验收 ID，不允许泛泛说“优化网关”。
2. 每个验收 ID 必须先写测试或验证记录模板。
3. 涉及真实额度的步骤必须等待人工确认。
4. 改完后更新本文“当前判断”或另写实施记录。
5. 如果某项只能部分完成，必须保留为“部分完成”，不能改成“已完成”。
6. 最终汇报必须列出：完成的 ID、证据、未完成 ID、是否达到 S2。

## 8. 新窗口目标模式提示词

```text
目标：按硬验收清单推进当前项目成为“合格生产生图生视频网关”，不要再按模糊口径判断完成。

先读取并以这些文档为事实来源：
- docs/plans/2026-07-10-image-video-gateway-production-acceptance-checklist.md
- docs/plans/2026-07-09-production-image-video-gateway-plan.md
- docs/plans/2026-07-09-gateway-gap-analysis-and-target-prompt.md

执行规则：
- 先对照硬验收清单输出当前仍未绿的验收 ID。
- 本轮只选择 1 到 3 个验收 ID 实现，不要泛泛大改。
- 先写测试或验证记录模板，再改代码。
- 不要消耗真实账号额度，除非我明确同意。
- 不要提交 config.json、accounts.db、cookie、token、API Key、真实账号信息。
- 完成后更新硬验收清单或新增实施记录。

推荐优先级：
1. P2-05/P2-06 视频实验室和验证样本库。
2. P2-02/P2-03/P2-04 在人工确认后做真实高级视频验证。
3. P4-04 自动账号维护。

完成后必须运行：
- python -m unittest discover -s tests -p "*_tests.py"
- python -m py_compile server.py banti_token_generator.py
- node 后台 JS parse 检查
- git diff --check
- git diff 敏感信息扫描

最终输出：
- 完成了哪些验收 ID。
- 每个 ID 的证据。
- 仍未完成哪些 ID。
- 当前状态是 S1 还是 S2。
```
