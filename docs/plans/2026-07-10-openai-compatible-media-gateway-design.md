# OpenAI-Compatible Image and Video Gateway Design

## 1. Goal and release boundary

The gateway must expose a stable, customer-facing media API while continuing to use the existing Oreate account pool and task engine internally. “Compatible” means an OpenAI SDK or an HTTP client using OpenAI-shaped requests can create images, create and inspect video jobs, list media models, and receive OpenAI-shaped errors without learning Oreate chat, account, cookie, or hydration details.

The release boundary is stricter than adding route aliases. A production release also requires encrypted account secrets, safe administrator rendering, bounded uploads and waits, durable idempotency, quota enforcement on every cost-bearing action, recoverable task state, reproducible dependencies, and current verification evidence. The existing `/v1/generate`, `/v1/uploads`, and `/v1/tasks/*` APIs remain supported as native extensions; OpenAI compatibility is an adapter over the same task records rather than a second execution system.

Because official OpenAI documentation could not be fetched in the current sandbox, the video surface deliberately supports both the resource-oriented form (`POST /v1/videos`) and the widely used generation alias (`POST /v1/videos/generations`). The resource form is canonical. Compatibility-specific behavior is isolated so the contract can be adjusted without changing provider execution.

## 2. Architecture

Introduce a small `gateway` package instead of adding more unrelated logic to the 6,000-line `server.py` module:

- `gateway/openai_compat.py`: request normalization, model aliases, task/video identifiers, status mapping, response builders, and OpenAI error envelopes. It contains no database or network access.
- `server.py`: FastAPI route wiring, authentication, calls into the existing account selector/task queue, and controlled media download proxying.
- Existing task rows remain the source of truth. OpenAI video IDs are reversible opaque strings such as `video_<task-id>` and are always resolved with the authenticated API key.
- Provider model names remain discoverable, while stable aliases map `gpt-image-1` to the configured default image model and `sora-2` to the configured default video model. Native provider names may also be passed directly.

This separation keeps the first migration small while creating a seam for future provider adapters. A later provider interface can replace `OreateClient` without changing public response builders.

## 3. Public compatibility contract

### Images

`POST /v1/images/generations` accepts `model`, `prompt`, `n`, `size`, `quality`, `response_format`, and optional gateway extensions `ratio`, `resolution`, and `timeout`. Only `n=1` and URL responses are initially supported; unsupported values return OpenAI-shaped `invalid_request_error` responses. The route queues the existing image task and waits up to a bounded server-controlled timeout. Success returns:

```json
{"created": 1710000000, "data": [{"url": "https://...", "revised_prompt": "..."}]}
```

The adapter maps common sizes to ratios and resolutions when possible, but explicit validated extensions win. A timeout does not create another task; the error includes the native task ID in a non-sensitive detail field so clients can use `/v1/tasks/{id}`.

### Videos

`POST /v1/videos` and `POST /v1/videos/generations` accept `model`, `prompt`, `seconds`, `size`, and optional `input_reference`/gateway scene fields. They return an asynchronous object with `id`, `object: "video"`, `created_at`, `status`, `model`, `seconds`, `size`, and `progress`.

`GET /v1/videos/{video_id}` returns the current mapped task status. `GET /v1/videos/{video_id}/content` streams or redirects only a validated HTTPS asset from the configured Oreate CDN allowlist after ownership and completion checks. `DELETE /v1/videos/{video_id}` maps to task cancellation but refuses already completed/failed terminal jobs.

`GET /v1/models` returns only models visible to the authenticated key’s kind/model/resolution/duration/experimental policy.

## 4. Errors, security, and consistency

Compatibility routes use the OpenAI envelope:

```json
{"error": {"message": "...", "type": "invalid_request_error", "param": "size", "code": "invalid_size"}}
```

Request validation errors, authentication errors, quota failures, upstream failures, and task-state conflicts all use this shape. Native routes retain their current gateway envelope.

API keys remain tenant boundaries. Compatibility responses never expose internal account IDs, raw upstream SSE/history payloads, cookies, upload session keys, or provider error bodies. Model discovery must apply the same policy later enforced at generation time.

Idempotency is reserved atomically before task creation, scoped to the API key and route, and expires according to configuration. Retry is a new billable attempt: it must re-check key state, scopes, quotas, account health, and balance. Uploads have configured byte/type limits and participate in request-rate accounting. All user-controlled strings rendered in the administrator UI are escaped.

## 5. Task recovery and operations

Startup recovery moves stale `running` tasks to a recoverable state or marks them expired according to configured leases. Each claim stores a lease timestamp/owner; a crashed worker cannot strand work indefinitely. A task is assigned an account when execution begins, not permanently at enqueue time, so queued bursts can be balanced and failed accounts can be replaced safely.

Readiness verifies database schema, encryption configuration, a live worker, the Banti/Node dependency, and at least one schedulable account. Health remains a shallow process probe. Metrics must be bounded and must not expose customer cost/account details anonymously.

Deployment is reproducible from tracked files: Python runtime dependencies include multipart support, development dependencies include the test client, Node’s minimum version is documented, the dynamic Banti source behavior is explicit, schema migration is versioned, and CI runs syntax, unit, security, and contract tests. Real low-cost image, text-video, and enabled upload-video combinations remain a manual approval gate because they consume upstream credits.
