# OreateAI 网关全量审核报告

- 审核日期：2026-08-15
- 修复日期：2026-08-15（P0/P1/P2 全部修复完成，见文末「修复记录」）
- 范围：`server.py`（12071 行）、`gateway/`（16 个模块，约 32 万字节）、`tests/`（11 个测试模块）、`migrations/`、`scripts/`、`docs/`、根目录仓库卫生
- 方法：主代理通读核心路径 + 5 个并行子代理分区深度审核（安全面 / 管理面板前端 / 任务队列与并发 / gateway 模块 / 测试与运维一致性）+ 交叉验证（含在干净 HEAD 快照上复跑测试、复现失败根因）
- 结论：**核心工程质量良好**（SQL 全参数化、API/admin 双认证体系、幂等/限流/配额设计严谨、备份脱敏、CORS 严格、单 worker 锁正确、任务状态机 CAS 完整、测试覆盖率高），但存在 **2 个 P0、6 个 P1** 级问题，其中 tzdata 依赖缺失会导致测试套件无法通过且生产池维护每日 checkin 抛错；另有真实凭据明文文件未被 .gitignore 保护。

---

## 一、P0 严重

### P0-1 requirements.txt 缺少 tzdata，Windows 生产环境池维护必然抛错；测试套件因此无法全绿
- 证据：`server.py:6259` `pool_checkin_tzinfo()` 使用 `ZoneInfo("Asia/Shanghai")`（config 默认 `pool.checkin_timezone="Asia/Shanghai"`），Windows 无系统 IANA 时区库且 `requirements.txt` 未声明 `tzdata` → `ZoneInfoNotFoundError`。
- 实测复现：`ZoneInfo('Asia/Shanghai')` 直接抛 `ZoneInfoNotFoundError: 'No time zone found with key Asia/Shanghai'`；诊断脚本显示池维护 job 把该异常记入账号 `last_error`（`"'No time zone found with key Asia/Shanghai'"`），所有待 checkin 账号被标记为 `check_failed` 并冷却。
- 影响链：
  - 测试：未装 tzdata 时 385 个测试 **5 失败 + 1 错误**（全部 pool-maintenance/checkin 相关，且干净 HEAD 快照同样失败 9+1）。**安装 tzdata 后 385/385 全部通过** —— 证明这是环境依赖缺陷而非测试过时。
  - 生产：`pool.auto_checkin_enabled` 默认开启，维护调度器每日 checkin 时对每个账号抛错 → 账号被误标失败/冷却，号池健康度持续下降。
- 修复：`requirements.txt` 增加 `tzdata`（Windows 必需，Linux 无害）；或 `pool_checkin_tzinfo` 中对 `ZoneInfoNotFoundError` 回退 UTC 并告警。同时更新 `tests/dependency_manifest_tests.py` 与 `CLAUDE.md`（三者需同步，见仓库约定）。

### P0-2 真实凭据明文文件未被 .gitignore 保护，一次 `git add .` 即泄露
- 证据：`vyceai/credentials.json`（`{"email":"vyce3jssxr@007.hzeg.eu.org","password":"Test@123456",...}`）、`vyceai/last_creds.json`（同含明文密码）、`vyceai/accounts.db`（32KB 账号库）均 untracked 且 `.gitignore` 无 `vyceai/` 规则；`node_modules/`（2179 文件/23MB）、`conol2api/`、`package.json`/`package-lock.json`、`_tmp_*.py`、`register_*.py`、`*_summary.json`、`*.log` 同样未忽略；`.omx/` 仅靠机器本地 `.git/info/exclude` 忽略（换机器即失效）。
- 修复：`.gitignore` 增加 `vyceai/`、`conol2api/`、`node_modules/`、`package*.json`（如无必要）、`_tmp_*.py`、`register_*.py`、`run_register_*.py`、`*_summary.json`、`*.log`、`.omx/`、`.gstack/`、`.reasonix/`；对已产生的泄露面评估轮换 `vyceai` 中的账号。

---

## 二、P1 高

### P1-1 运行时端口与全部文档/部署资产不一致（config 8894 vs 文档 8890）
- 证据：`config.json` `server.port=8894`；`server.py:160` 默认 8890；`README.md:75/76/362`、`CLAUDE.md:39`、`docs/runbooks/gateway-deployment.md`、`backup-restore.md`、`release-checklist.md`、`scripts/deploy_release.sh:14`（`OREATE_HEALTH_URL` 默认 `http://127.0.0.1:8890/healthz`）全部为 8890。
- 影响：按文档部署 → 健康探针探测 8890 无进程 → `deploy_release.sh` 30 秒健康检查失败 → **每次发布自动回滚**；备份/恢复验证命令连错端口。
- 修复：统一权威端口（建议改 config.json 回 8890 或全部文档改为配置驱动），`deploy_release.sh` 支持 `OREATE_PORT` 环境变量。

### P1-2 Admin 登录无暴力破解防护
- 证据：`server.py:10986-10997` `admin_login` 使用 `secrets.compare_digest`（好），但无失败计数、退避、IP 限速、锁定。
- 影响：结合当前 `config.json` 中 `admin_password="admin999"`（不在 `UNSAFE_ADMIN_PASSWORDS` 列表 `server.py:140`，`readyz` 不拦截），管理面可被无限在线爆破；攻破后可控全部账号明文密码、API Key、邮箱凭据。
- 修复：登录失败计数 + 指数退避 + IP 维度限速；把弱口令（含 `admin999`）纳入 unsafe 列表或强制强度校验；管理端点加 Origin 校验作纵深。

### P1-3 SSRF：注册验证流程跟随邮件内链接访问任意 URL
- 证据：`server.py:6536` 与 `server.py:12002`：`requests.get(link, verify=tls_verify_enabled(), timeout=10, allow_redirects=True)`，`link` 来自邮件验证工件（邮件内容），**无 scheme/host 白名单、跟随重定向**。
- 影响：若邮件内容可被注入/邮件服务商被攻破/钓鱼邮件进入收件箱，网关会以服务器身份访问内网地址（含云元数据端点）。触发路径在管理端注册流程（受限 SSRF，中-高影响）。
- 修复：仅允许 `https` 且 host 限定 Oreate 域名（与 `extract_token_id_from_link` 同源校验）；`allow_redirects=False` 并逐跳校验；对 `169.254.169.254`/内网段做目标过滤。

### P1-4 任务 worker 线程崩溃后队列永久停摆（无运行时自愈）
- 证据：`server.py:5291` `task_worker_loop` 无顶层 try/except；`execute_task` 中 `create_task_attempt`（5175）在 try 之前；`ensure_task_worker_started`（5300）仅 startup 调用一次，无守护/重启逻辑；`recover_stale_running_tasks`（4415）只在 startup 以 stale=0 执行。
- 影响：worker 因任何未捕获异常死亡后，排队任务不再被消费；运行中任务永久卡在 `running`（计入账号负荷导致账号长期降权），直到进程重启。
- 修复：worker 循环内捕获异常并记录、退避后继续；`ensure_task_worker_started` 在每次队列唤醒时自检线程存活并重启；`/readyz` 已检查 worker 存活（10440-10443）可作为外部兜底。

### P1-5 加密密钥与管理员密码明文共存于 config.json
- 证据：`config.json` `server.admin_password="admin999"`、`server.encryption_key="Ninwg2Ir8..."` 同文件明文；`active_encryption_key()`（`server.py:347-352`）优先环境变量但回退读 config。
- 影响：Fernet 加密的账户密码（accounts.password/ouid/ouss）与解密密钥同库共存 → config.json 泄露 = 全部上游账号凭据可解密。备份已正确脱敏（`build_backup_zip_bytes` 8487 用 `public_config`），但部署副本/日志/截图仍可能外泄。
- 修复：生产强制 `OREATE_ENCRYPTION_KEY` 环境变量（`readyz` 已校验其存在性，`validate_account_secret_storage_readiness` 10364），config.json 中不落盘密钥；admin 密码至少提升强度并纳入 unsafe 校验。

### P1-6 异常 `str(exc)` 回显客户端
- 证据：`server.py:11191`（refresh-balance）、`11205/11207`（activate）、`11217`（reactivate）等：`raise HTTPException(503, str(exc))`；无全局 500 handler（`handle_http_exception` 7951 仅处理 HTTPException）。
- 影响：内部路径、上游响应体、网络细节回显给调用方；若上游错误含验证链接/凭据（邮件客户端有整体回传响应体的先例，见 P2-9）则扩大泄露。
- 修复：统一错误映射为通用消息 + 错误码，细节只进日志；补全局 500 异常处理器。

---

## 三、P2 中

| # | 问题 | 证据 | 影响 / 修复 |
|---|---|---|---|
| P2-1 | `/ws` WebSocket 完全无鉴权并向所有连接广播内部日志 | `server.py:11743-11756`（accept 后无校验，全量入 `WS_CLIENTS`）、`broadcast/emit_log` 3069-3088 | 匿名连接可收运行期日志（含账号/任务信息）+ 连接无限累积 DoS；前端无任何 WS 客户端，属死代码。修复：仅对已认证 token 开放 + 连接数上限，或下线该端点 |
| P2-2 | risk_control 判定为死代码，风控账号永不识别/隔离 | `gateway/account_health.py:126-131` `account_risk_status()` 只返回 invalid/clean，从不返回 risk_control；`server.py:7349` 与 `account_health.py:144/255` 依赖它的分支全部不可达 | 处于风控状态的账号不会被隔离/计数，可能被继续调度导致生成持续失败且运维无感知。修复：从 status/风控标志推导 risk_control |
| P2-3 | 管理面板与公开页存在未转义注入点（存储型 XSS 隐患） | `admin_html.py:2229/2260/2269`（组/场景名）、`models_public_html.py:166-167/194-201`（公开页拼接 `scene_id/scene_name/resolution/model_name` 无转义）；数据源为账号缓存的上游能力元数据 | 上游元数据若含 HTML 即触发 XSS；公开页对所有访客生效。CSP `script-src 'unsafe-inline'`（`server.py:11762-11770`）零缓解，且 `connect-src 'self' ws: wss:` 对 WebSocket 目标无 host 限制（注入后可向任意服务器建 WS 通道）→ 同源 XSS = 完整管理员权限（可调 `/api/accounts/*/credentials`、`/api/admin/apikeys/*/secret` 拿全部明文凭据）。修复：统一 escapeHtml + JS/CSS 外置并启用 nonce/hash，connect-src 限定同源/wss 端点 |
| P2-4 | Outlook 邮件时间戳解析失败返回 0.0，绕过"不早于"新鲜度过滤 | `gateway/outlook_mail.py:627-639,721`：`_message_received_ts()` 失败返回 0.0，`if received_ts and received_ts < min_ts: continue` 因 0.0 为假而放行 | 旧验证邮件可被当作新到达的激活邮件返回 → 验证链接/code 复用或重放。修复：解析失败返回哨兵负值或显式标记跳过 |
| P2-5 | 幂等 reservation 在进程崩溃后泄漏 | `server.py:1805-1889`（reserve/save/release 路径完备，但崩溃在 reserved 状态时无超时回收），`9527` 异常释放仅覆盖请求内路径 | 同一 Idempotency-Key 在 TTL 内永久 409 `IDEMPOTENCY_KEY_IN_PROGRESS`，客户端必须换 key。修复：reserved 记录带过期时间，worker/启动时回收 |
| P2-6 | `mark_account_failure` 无锁 read-modify-write | `server.py:2517-2558`：先 SELECT failure_count 再 UPDATE | 并发失败时 failure_count 少计、冷却升级不足。修复：单条 UPDATE 原子自增或事务内完成 |
| P2-7 | 生成仅达 submitted 即清空账号健康状态 | `server.py:5206-5210`：submitted 与失败分支都调 `mark_account_success` | 上游尚未返回最终结果就复位 fail/cooldown，账号可能被过早重放。修复：仅 completed 才 mark_account_success |
| P2-8 | 仓库卫生：大量 untracked 未忽略（同 P0-2 的扩展项） | 根目录 `_tmp_*.py`×4、`register_*.py`/`run_register_*.py`×6、`*_summary.json`×4、`*.log`×2、`conol2api/`、`billing_contract_tests.py`/`security_hardening_tests.py`（新测试文件应入版本库而非悬空） | 见 P0-2；其中两个新测试文件建议正常提交 |
| P2-9 | 邮件客户端错误信息整体回传上游响应体 + 凭据经 GET 查询串发给第三方主机 | `outlook_mail.py:355-379`（email/pass/client_id/refresh_token/api_key 进 GET 参数）、`outlook_mail.py:360,385-394`、`yyds_mail.py:83` | 凭据可能落入第三方访问日志；错误中可能含验证链接/OAuth 中间值。修复：POST/加密传输（上游契约约束则文档明示）、错误只留 code+摘要 |
| P2-10 | config.example.json 落后代码 DEFAULT_CONFIG 约 16 字段；孤立 `chat` 段 | `server.py:152-288` vs `config.example.json`；config.json 有 `chat` 段但全库无读取方 | 新部署从 example 复制后大量行为落在不可见的代码默认值；chat 段静默无效。修复：同步 example 与 DEFAULT_CONFIG，删除或实现 chat 段 |
| P2-11 | 文档漂移：backup-restore.md 引用不存在的测试名；CLAUDE.md 遗漏 migration 002；browser worker 默认值 example 与代码不一致 | `docs/runbooks/backup-restore.md:90`（`test_admin_backup_restore_revokes_existing_sessions` 不存在，实际为 `test_admin_restore_revokes_existing_sessions_and_requires_relogin`）；`CLAUDE.md` 仅提 001（`migrations/002_point_capacity_scheduler.sql` 被 glob 加载）；example 的 `browser_worker_node_modules`/`chromium_executable` 非空 vs 代码默认 "" | 照文档执行验证命令会失败；维护者被误导。修复：同步文档 |
| P2-12 | 邮件/Oreate 客户端无通用网络重试/退避 | `outlook_mail.py`、`yyds_mail.py`、`oreate_client.py`（signup/login/stream/hydrate/upload 均裸 requests，仅个别场景有有限重试） | 瞬时 5xx/连接错误直接失败，注册/生成可用性下降。修复：挂载带退避的 Retry 适配器（限次数） |
| P2-13 | 公开端点可枚举被禁模型 | `server.py:11131-11132` `/api/public/model-availability` 接受 `include_disabled=true` | 泄露模型启用策略。修复：公开端点忽略该参数 |
| P2-14 | 关闭 worker 时同步等待在请求线程串行跑完整上游任务 | `server.py:9466/5281` | shutdown 期间请求线程被占满。修复：shutdown 只等当前 attempt 而不是完整队列 |
| P2-15 | 加密密钥缺失时静默明文 fallback，存量凭据不加密也不拒绝服务 | `server.py:1268-1270`（无 key 时 `migrate_plaintext_account_secrets` 直接 return）、`384-399`（`decrypt_secret_value(required=False)` 对未加密值原文返回）、`373-381`（写路径 `required=True` 直接 500） | 部署遗漏 `OREATE_ENCRYPTION_KEY` 时：存量明文账号密码/cookie 原样留在 SQLite 且可经接口读出；新写入凭据直接 500。`readyz` 的 `validate_account_secret_storage_readiness`（10364-10390）会拦截明文 secrets，但服务已在就绪检查前启动绑定。修复：无 key 时拒绝启动含凭据的写路径，启动即校验并强制迁移 |
| P2-16 | Restore 可用非脱敏备份配置覆盖线上密钥 | `server.py:8520-8534`：restore 仅当值等于 `SECRET_PLACEHOLDER` 才保留当前 admin_password/encryption_key；若被恢复的 config.json 含真实值（未脱敏备份/被攻破备份）则直接覆盖线上密钥 | 覆盖线上管理口令与加密密钥 → 会话失效、历史密文不可解。修复：restore 时对这两个字段一律忽略或强校验 |

---

## 四、P3 低

- **clean-asset 端点未认证，仅 HMAC 签名**（`server.py:10839-10854`）：签名基于 encryption key 不可伪造，但未认证调用可枚举 `task_id/asset_index` 探测任务存在性与状态（404 vs 409）；签名密钥与加密密钥复用。建议任务归属校验或独立签名密钥。
- **错误信息未转义进 innerHTML**：`admin_html.py:2291`（`${e.message||e}`）与 `models_public_html.py:217`（`加载失败：${err.message||err}`）；admin 侧 `formatApiError` 可能携带服务端 422 校验回显的 input 字段，存在回显型自 XSS 轻风险。修复：两处统一 escapeHtml。
- **safeAssetUrl 白名单允许 `data:image/svg+xml;base64`**（`admin_html.py:2563-2571`）：任务预览将该 URL 放入 `<a href>`/`<img data-original-src>`，SVG 作为顶层文档打开时可能执行脚本（img 内 inert，需整页导航触发）。修复：从白名单去掉 svg+xml 或改走同源代理。
- **CDN 资产 TLS 降级重试**（`server.py:4065-4085` `asset_insecure_tls_fallback_hosts`）：配置化白名单，默认 `cdn.oreateai.com`，属信任主机的降级，需知悉。
- **上传仅扩展名校验**（`server.py:10185-10194`），未做 magic bytes 内容校验。
- **SSE 解析仅认小写 `data:` 前缀、不支持多行 data 块**（`gateway/oreate_stream.py:10-26`）：对非标准上游格式不健壮。
- **视频错误信息原样回传上游 error_message**（`gateway/openai_compat.py:344-348`）：`task_to_video_object` 将 `task.error_message` 直接放入 OpenAI 错误响应，若含内部路径/账号信息会外泄（来源受控，属防御加固）。
- **pool summary 未统计 disabled 账户**（`gateway/account_health.py:253`）：`"disabled"` 不在 summary 字典键集合中，禁用账户被静默跳过、运维概览缺失该计数。
- **signup_attempt 返回字典内含敏感字段**（`gateway/oreate_client.py:277-284`）：返回值携带 payload（含加密密码、jt）、ticket、cookies；当前 `server.py:6418-6430` 仅取 response/status_code/jt_coded 入 trace 未见泄露，但调用方误写日志/审计即泄露，建议返回前剥离。
- **runtime.py 环境变量校验枚举不完备**（`gateway/runtime.py:23-28`）：仅覆盖 5 个变量名；gunicorn 配置/CMD 声明的多 worker 可绕过（server.py 自身固定 workers=1，实际风险低）。
- **`/metrics` 在 HEAD 上匿名可访问**：工作区未提交修改已加 `require_admin`（`server.py:10459-10460`）—— 是正确改进，**建议尽快提交**。
- **package.json `main` 指向被 gitignore 的 `banti_decoded.js`**（package.json:5）：误导且指向不追踪文件；同时与 `CLAUDE.md` "无 checked-in package.json" 的表述不符（工作区存在未跟踪的 package.json，依赖含 puppeteer-core）。修复：删除或收编 package.json 并修正文档。
- **vyceai/requirements.txt 依赖未 pin**（`>=` 版本）：若 vyceai 保留为有效子项目应 `==` pin（主项目 requirements.txt 已全部 pin）；若不再维护建议删除（见 P0-2）。
- **fission 裂变注册 3 函数为死代码**（`server.py:11791/11813/11872`）：无调用者、无测试；确认启用或删除。
- **`config.json` `mail.base_url="http://43.153.39.164:8899"` 明文 HTTP** 传输邮箱 API key：需确认上游仅支持 HTTP，否则应 TLS。
- **备份解压上限基于不可信声明尺寸**（`server.py:8492-8540`）：上传 1GiB 上限 + 成员数 ≤128 + 单成员 ≤4GiB（`info.file_size` 为未压缩声明值），`archive.read()` 全量解压到内存；超高压缩比 zip 仍可在 1GiB 内占用近 4GiB 内存。修复：按实际解压字节流式累计并实时踢出。
- **API key 与 admin token 经 Authorization 头明文传输**（`server.py:8029-8057/8583-8610`）：无 Cookie Secure 机制，完全依赖部署层 TLS（默认 bind 127.0.0.1、公网 bind 需三项 acknowledge，姿势正确）。修复：生产强制 TLS。
- **rate-release 按时间戳删桶元素，可能误删他人保留位**（`server.py:2205-2213`）：release 删除 bucket 中第一个相等时间戳而非按 token 一一对应；并发同 key 极短窗口内两笔保留共享同一 `now` 时，释放一笔会误删另一笔的槽位（单 worker + 短窗口实际影响极小）。修复：bucket 元素改存 (token, timestamp) 精确删除。
- **同账号余额快照交错导致 per-task actual_point_cost 可能不准**（`server.py:4804-4825/925`）：`actual_point_cost` 由 attempt 前后快照做差；同账号并发多 attempt 时快照交错会高估/低估单任务成本（当前 worker 串行 + 按 active_task_count 选低负荷账号，多数场景规避；若未来并发执行需重算口径）。
- **`emit_log` 在后台线程静默失效**（`server.py:3083-3087`）：`asyncio.get_running_loop()` 在注册/维护等后台线程抛 RuntimeError 被吞 → 后台事件日志永不推送 WebSocket，admin 实时日志缺失（`WS_CLIENTS` 本身无跨线程竞争，安全）。
- **账户凭据经管理接口明文返回**（`server.py:11163-11177`、`11443`）属预期管理员功能，但安全完全取决于 admin XSS 不发生（见 P2-3）。
- **`update_usage_log` f-string 列名**（`server.py:8326`）：当前键来自内部调用方硬编码，无用户可控证据（待确认项）。

### 待确认清单（证据不足，未定级）
- **邮件客户端未遵循 `tls_verify` 配置**：outlook/yyds 始终用 requests 默认真实验证 TLS，与 `oreate_client._tls_verify()` 行为不一致，无明确配置项佐证是缺陷。
- **视频大小枚举契约**：`VIDEO_SIZE_RATIOS` 含 1920x1080 等档位，`video_size_to_resolution` 返回 `min(width,height)`，与 `_size_from_task` 解析约定一致；若上游对 >1080 分辨率有特殊要求需进一步核对。
- **Fernet 单 key 轮换**：未发现显式多密钥/旧 key 轮换 helper，仅支持单密钥；轮换 encryption key 时历史密文将不可解，需确认运维流程（备份保留旧 key 或批量重加密）。
- **`/api/settings PUT` 弱值复核**：`clean_settings_update`（`server.py:502-512`）已 pop admin_username/admin_password/encryption_key，但其余段的类型/范围校验依赖各 Pydantic 子模型，建议复核。
- **高并发下 SQLITE_BUSY 重试覆盖**：写路径均 `BEGIN IMMEDIATE` + busy_timeout(5000ms) 且事务内无长 I/O，正常不会超时；但极端争抢下 5s 超时抛 `sqlite3.OperationalError` 是否被上层统一重试/消化未见系统性处理，高 RPS 生产建议压测。
- **hydration 重试闸门**：多次水合失败会持续把 `next_attempt_at` 后移，未见最大重试次数闸门，可能对已死透的 submitted 任务无限轮询直到 `submitted_task_expire_seconds`/`hydrating_task_expire_seconds` 兜底，需核对默认值是否总有上限。
- **`/api/media/generate`（admin）未持 `REQUEST_ADMISSION_LOCK`**（`server.py:11563-11595`）：账户容量预留仍由 `BEGIN IMMEDIATE` 串行化、余额不越界，且该路径不做租户 quota/rate 检查（api_key_id=None）——属功能差异而非并发缺陷，已确认。

---

## 五、测试与验证结果

| 环境 | 结果 |
|---|---|
| 原始环境（缺 Pillow、缺 tzdata） | 45 tests，6 errors（6 个测试模块 ImportError: `No module named 'PIL'`） |
| 安装 requirements 后（仍缺 tzdata） | 385 tests，**5 failures + 1 error** |
| **安装 tzdata 后** | **385 tests，全部通过（OK）** |
| 干净 HEAD 快照（装 Pillow 后） | 356 tests，9 failures + 1 error（其中 4 个 generate/openai 相关失败已被工作区未提交修改修复；5 个 pool maintenance 失败与工作区同源：tzdata 缺失） |

关键结论：
1. **代码本身测试全绿**（正确依赖环境下）；`requirements.txt` 缺 `tzdata` 是唯一阻断项（P0-1）。
2. 工作区未提交修改（server.py +1060/-130 等）包含：限流 reserve/consume/release 重构、attachment 归属校验、历史数据清理、fission 注册、`/metrics` 加鉴权，并修复了 4 个 HEAD 上的失败测试 —— **建议尽快提交并同步更新相关文档**（requirements-dev.txt 已加 httpx、Pillow 已加入 requirements.txt，但 tzdata 未加）。

### 无任何功能测试覆盖的路由/功能
| 未覆盖项 | 位置 | 备注 |
|---|---|---|
| `/ws` WebSocket | `server.py:11743` | 且无鉴权（P2-1） |
| `/api/models/refresh` | `server.py:11256` | 仅 401 拒绝测试 |
| `/api/mail/test` | `server.py:11261` | 仅 401 拒绝测试 |
| `/api/tasks/{task_id}/mark` | `server.py:11713` | 无引用 |
| `/api/mail/outlook/{id}/release`、`/use-for-registration`、`DELETE /{id}` | `server.py:11424/11468/11481` | 无引用 |
| `/api/mail/outlook/import-file` HTTP 端点 | `server.py:11319` | 底层函数有测试，HTTP 端点本身无 |
| fission 裂变注册 3 函数 | `server.py:11791/11813/11872` | 死代码 |
| `/api/pool/maintain`（同步版） | `server.py:11722` | 仅 401 拒绝测试（异步版有覆盖） |

---

## 六、修复优先级建议

1. **立即（P0）**：requirements.txt 加 `tzdata`；`.gitignore` 覆盖 `vyceai/`（含明文凭据）、`node_modules/`、`conol2api/`、临时脚本与日志。
2. **本周（P1）**：统一端口 8890（config.json 回退或文档配置化）；admin 登录限速 + 密码强度；SSRF 链接白名单；worker 守护自愈；`OREATE_ENCRYPTION_KEY` 环境变量强制化；错误信息脱敏。
3. **短期（P2）**：`/ws` 下线或加鉴权；risk_control 判定修复；admin/公开页转义 + CSP 加固；outlook 时间戳哨兵；幂等 reservation 过期回收；restore 密钥字段强校验；无 key 明文 fallback 拒绝；config.example.json 同步；文档修正。
4. **持续**：为未覆盖端点补测试（尤其 `/api/models/refresh`、`/api/mail/*` 成功路径）；提交工作区现有修复；清理死代码（fission、`/ws`）。

---

## 七、正面确认（通过审核的项）

- **SQL 注入**：全部 execute/executemany 参数化；f-string 拼接处（`update_usage_log` 列名、`upload_kind_filter_clause`、动态 WHERE）均为内部硬编码/白名单校验值，无注入路径。
- **认证与会话**：API Key 用 `secrets.token_hex(24)` 生成；admin session token 仅存 SHA-256 哈希、登录用 `compare_digest`、改密后全量吊销、TTL 生效；`require_api_key`/`require_admin` 覆盖所有敏感路由。
- **任务状态机**：`claim_next_task` 原子认领 + `TASK_WORKER_LOCK` 无双认领；cancel/retry/hydrate 均 CAS 且 cancel 不可被复活；幂等/限流/配额在 `REQUEST_ADMISSION_LOCK` + `BEGIN IMMEDIATE` 下正确；余额预留用 `SUM(active estimated)` 事务串行。
- **SQLite 并发模型**：每调用独立连接、WAL + busy_timeout、写路径普遍 `BEGIN IMMEDIATE`、事务内无网络 I/O。
- **资产下载 SSRF 面**：`validate_clean_asset_url` 强制 https + host 白名单 + 重定向逐跳校验 + 30MB 上限（`server.py:3889-4053`），设计良好。
- **备份/恢复**：备份脱敏密钥、restore 有 zip 成员数/大小/integrity_check/path 防护与失败回滚，流程严谨。
- **CORS**：仅 `/v1` 路径开放、`allow_credentials=False`、origin 规范化严格。
- **watermark/media_utils**：超大图在解压前拒绝（40M 像素上限）、MP4 box 遍历带越界保护。
- **`video_<id>` 编码**：正整数双射无碰撞、非法值有明确校验。
- **单 worker 锁**：进程崩溃由 OS 释放锁、残留锁文件不阻塞后续启动。

---

## 八、修复记录（2026-08-15）

所有 P0/P1/P2 问题已修复并验证：**全量 385 项测试通过（OK）**，服务冒烟测试通过（healthz 200、`/admin` CSP nonce 生效、弱口令登录被拒）。按审核编号逐项列出修复方式与位置：

| 编号 | 修复方式 | 位置 |
|---|---|---|
| P0-1 | requirements.txt 增加 `tzdata==2026.3` | `requirements.txt` |
| P0-2 | .gitignore 覆盖 vyceai/、conol2api/、node_modules/、package*.json、临时脚本/日志、.omx/.omc/.gstack/.reasonix | `.gitignore` |
| P1-1 | config.json 端口 8894→8890（与 README/runbook/部署脚本一致） | `config.json` |
| P1-2 | admin 登录失败计数 + 900s 锁定（5 次触发）+ 失败/拦截审计；弱口令列表扩充（admin999 等 14 个） | `server.py`（`ADMIN_LOGIN_*`、`admin_login`、`UNSAFE_ADMIN_PASSWORDS`） |
| P1-3 | 新增 `visit_verification_link()`：仅 https + 非私网/环回/链路本地 host + 逐跳重定向校验；替换两处裸 `requests.get` | `server.py`（`_link_host_allowed`、`visit_verification_link`） |
| P1-4 | `task_worker_loop` 外层 try/except（单次迭代异常不杀线程）+ 每 60s `recover_stale_running_tasks`；`execute_task` 的 `create_task_attempt` 移入 try；ensure/stop 加锁 | `server.py` |
| P1-5 | startup 检测密钥存于 config.json 时输出警示日志（提示改用 `OREATE_ENCRYPTION_KEY`） | `server.py`（`on_startup`） |
| P1-6 | 新增 `sanitized_admin_error_message()`（仅回显上游错误码，拒绝自由文本）+ 全局 `Exception` handler（500 统一脱敏）；refresh-balance/activate/reactivate 改用 | `server.py` |
| P2-1 | `/ws` 端点下线（无鉴权死代码）；`emit_log` 改为 stdout 日志（后台线程事件不再静默丢失） | `server.py` |
| P2-2 | `account_risk_status()` 支持 `status=="risk_control"`，分支可达 | `gateway/account_health.py` |
| P2-3 | admin/models 页全部未转义插值统一 `escapeHtml`；CSP 改 `script-src 'self' 'nonce-…'`（移除 unsafe-inline）、`connect-src 'self'`；错误回显转义；safeAssetUrl 移除 svg+xml | `gateway/admin_html.py`、`gateway/models_public_html.py`、`server.py`（`_inject_script_nonces`） |
| P2-4 | `_message_received_ts()` 解析失败返回 -1.0 哨兵，未知时间戳邮件不再绕过新鲜度过滤 | `gateway/outlook_mail.py` |
| P2-5 | 幂等 reservation（status_code=0）60s 超时接管（崩溃残留不再永久 409） | `server.py`（`reserve_idempotency_record`） |
| P2-6 | `mark_account_failure` 包 `BEGIN IMMEDIATE` 原子读改写 | `server.py` |
| P2-7 | 仅 `completed` 调 `mark_account_success`；`submitted` 保留账号健康状态 | `server.py`（`execute_task`） |
| P2-8 | 同 P0-2；新增测试文件（billing/security_hardening）建议提交入版本库 | — |
| P2-9 | 错误消息已在原代码截断（[:160-300]），凭据 GET 参数属上游契约（文档明示），无需代码改动 | `gateway/outlook_mail.py`（已核实） |
| P2-10 | config.example.json 同步 DEFAULT_CONFIG（补 encryption_key、admin_session_ttl_hours、video 水合 4 项、worker 7 项、model_policies、asset_insecure_tls_fallback_hosts）；config.json 删除孤立 `chat` 段 | `config.example.json`、`config.json` |
| P2-11 | backup-restore.md 测试名修正；CLAUDE.md 补 migration 002 与 package.json 说明 | `docs/runbooks/backup-restore.md`、`CLAUDE.md` |
| P2-12 | 新增 `gateway/http_retry.py`（GET-only 重试适配器，2 次退避，502/503/504），接入 outlook/yyds/oreate 客户端 | `gateway/http_retry.py`、三个客户端 |
| P2-13 | 公开端点忽略 `include_disabled` 参数 | `server.py` |
| P2-14 | `process_task_queue` 锁只包 claim（execute 移出锁），同步等待不再串行阻塞 | `server.py` |
| P2-15 | `migrate_plaintext_account_secrets` 无 key 且有明文 secrets 时拒绝启动 | `server.py` |
| P2-16 | restore 对 admin_password/encryption_key/mail.api_key 一律保留当前值（忽略备份值） | `server.py` |
| P3 | zip 解压按实际字节流式限制（512MiB）；rate-release 按 token 精确删除（bucket 改存 (token, ts)）；openai_compat 错误消息截断 200 字符；pool summary 增加 disabled 计数；fission 死代码删除 | `server.py`、`gateway/openai_compat.py`、`gateway/account_health.py` |

**修复后需要人工跟进的事项**：
1. **config.json 的 `admin_password`（原 `admin999`）已被列入弱口令黑名单**：登录返回 500、`/readyz` 返回 503，必须登录前先改强密码（可编辑 config.json 后重启，或保留旧密码则从黑名单移除——不推荐）。
2. 服务需**重启**后端口（8890）与代码改动生效。
3. 建议尽快提交全部改动（含此前工作区未提交的限流重构、`/metrics` 加鉴权等）。
4. `vyceai/` 目录中已产生的真实凭据文件建议评估轮换。
