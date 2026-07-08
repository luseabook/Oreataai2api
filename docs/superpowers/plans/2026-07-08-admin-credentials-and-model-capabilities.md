# Admin Credentials And Model Capabilities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe administrator credential change flow and expose normalized image/video model capabilities for gateway clients and the admin UI.

**Architecture:** Keep the current single-file FastAPI structure, but add small helper functions around config mutation and capability normalization. API callers use `GET /v1/capabilities`; administrators use `POST /api/admin/credentials`, `GET /api/models/capabilities`, and `POST /api/models/refresh`.

**Tech Stack:** FastAPI, Pydantic, SQLite, unittest, vanilla HTML/JS embedded in `server.py`.

## Implementation Status

- Done: `POST /api/admin/credentials` verifies current password, rejects weak/mismatched updates, persists config, and invalidates existing admin tokens.
- Done: `/api/admin/settings` filters out `server.admin_username` and `server.admin_password`; credentials can no longer be changed through generic settings.
- Done: `GET /v1/capabilities`, `GET /api/models/capabilities`, and `POST /api/models/refresh` expose normalized image/video model capabilities from cached or refreshed account metadata.
- Done: Admin UI loads capabilities into model/resolution/ratio/duration/scene selectors and has separate credential-change controls.
- Verification target remains Task 5 below before reporting completion.

---

## File Structure

- Modify `server.py`: add request models, credential update helper, capability normalization helpers, new API routes, and admin UI controls.
- Modify `tests/security_regression_tests.py`: add regression coverage for credential changes, capability normalization, capability endpoints, and admin HTML controls.
- Keep `docs/superpowers/specs/2026-07-08-admin-credentials-and-model-capabilities-design.md`: design source of truth.
- Keep this plan as `docs/superpowers/plans/2026-07-08-admin-credentials-and-model-capabilities.md`.

## Task 1: Credential Change API

- [ ] Add failing tests in `tests/security_regression_tests.py`:

```python
def test_admin_credentials_change_requires_current_password(self):
    response = self.client.post(
        "/api/admin/credentials",
        headers=self.admin_headers(),
        json={
            "current_password": "wrong",
            "new_username": "admin2",
            "new_password": "new-password-123",
            "confirm_password": "new-password-123",
        },
    )
    self.assertEqual(response.status_code, 401)

def test_admin_credentials_change_rejects_weak_or_mismatched_password(self):
    headers = self.admin_headers()
    mismatch = self.client.post(
        "/api/admin/credentials",
        headers=headers,
        json={
            "current_password": "test-admin-password",
            "new_username": "admin2",
            "new_password": "new-password-123",
            "confirm_password": "different",
        },
    )
    weak = self.client.post(
        "/api/admin/credentials",
        headers=headers,
        json={
            "current_password": "test-admin-password",
            "new_username": "admin2",
            "new_password": "admin123",
            "confirm_password": "admin123",
        },
    )
    self.assertEqual(mismatch.status_code, 400)
    self.assertEqual(weak.status_code, 400)

def test_admin_credentials_change_updates_config_and_invalidates_tokens(self):
    headers = self.admin_headers()
    response = self.client.post(
        "/api/admin/credentials",
        headers=headers,
        json={
            "current_password": "test-admin-password",
            "new_username": "new-admin",
            "new_password": "new-password-123",
            "confirm_password": "new-password-123",
        },
    )
    self.assertEqual(response.status_code, 200)
    self.assertEqual(server.CFG["server"]["admin_username"], "new-admin")
    self.assertEqual(server.CFG["server"]["admin_password"], "new-password-123")
    old_token_response = self.client.get("/api/admin/settings", headers=headers)
    self.assertEqual(old_token_response.status_code, 401)
```

- [ ] Run the tests and verify they fail because `/api/admin/credentials` does not exist:

```bash
python -m unittest tests.security_regression_tests.SecurityRegressionTests.test_admin_credentials_change_requires_current_password tests.security_regression_tests.SecurityRegressionTests.test_admin_credentials_change_rejects_weak_or_mismatched_password tests.security_regression_tests.SecurityRegressionTests.test_admin_credentials_change_updates_config_and_invalidates_tokens
```

- [ ] Implement `AdminCredentialsIn` and route in `server.py`:

```python
class AdminCredentialsIn(BaseModel):
    current_password: str
    new_username: str
    new_password: str
    confirm_password: str


@app.post("/api/admin/credentials")
def update_admin_credentials(body: AdminCredentialsIn, _=Depends(require_admin)):
    global CFG
    current_password = str(CFG["server"].get("admin_password") or "")
    if not secrets.compare_digest(body.current_password, current_password):
        raise HTTPException(401, "current password is incorrect")
    new_username = body.new_username.strip()
    if not new_username:
        raise HTTPException(400, "new username is required")
    if body.new_password != body.confirm_password:
        raise HTTPException(400, "new passwords do not match")
    if len(body.new_password) < 8 or is_unsafe_admin_password(body.new_password):
        raise HTTPException(400, "new password is too weak")
    CFG = deep_merge(CFG, {"server": {"admin_username": new_username, "admin_password": body.new_password}})
    save_config(CFG)
    ADMIN_TOKENS.clear()
    return {"ok": True}
```

- [ ] Run all tests:

```bash
python -m unittest discover -s tests -p "*_tests.py"
```

## Task 2: Capability Normalization

- [ ] Add failing tests with sample model config dictionaries:

```python
def test_normalizes_image_and_video_capabilities(self):
    image_info = {
        "data": {
            "factory": [
                {
                    "modelFactoryName": "Nano Banana",
                    "models": [
                        {
                            "modelName": "Google Nano Banana 2",
                            "modelDesc": "Flagship 4K high-resolution",
                            "modelIcon": "image.svg",
                            "resolution": ["4K", "2K"],
                            "size": [{"ratio": "16:9"}, {"ratio": "1:1"}],
                            "pointCost": [{"resolution": "4K", "point": 12}],
                        }
                    ],
                }
            ]
        }
    }
    video_info = {
        "models": {
            "data": {
                "models": [
                    {
                        "modelName": "Seedance 2.0 Mini",
                        "description": {"zh": "视频模型说明", "en": "Video model"},
                        "modelIcon": "video.svg",
                        "duration": [5, 10],
                        "videoResolution": ["480", "720"],
                        "videoSize": [{"ratio": "16:9"}, {"ratio": "9:16"}],
                        "supportAudio": True,
                        "supportModifySize": True,
                    }
                ]
            }
        },
        "scenes": {
            "data": {
                "scenes": [
                    {
                        "sceneId": "text_or_image",
                        "sceneName": {"zh": "文生或图生视频", "en": "Text or image"},
                        "description": {"zh": "输入文本或图片"},
                        "sceneIcon": "scene.svg",
                    }
                ]
            }
        },
    }
    caps = server.normalize_capabilities(image_info, video_info)
    self.assertEqual(caps["image"]["models"][0]["name"], "Google Nano Banana 2")
    self.assertEqual(caps["image"]["models"][0]["resolutions"], ["4K", "2K"])
    self.assertEqual(caps["image"]["models"][0]["ratios"], ["16:9", "1:1"])
    self.assertEqual(caps["video"]["models"][0]["description"], "视频模型说明")
    self.assertEqual(caps["video"]["models"][0]["durations"], [5, 10])
    self.assertEqual(caps["video"]["models"][0]["resolutions"], ["480", "720"])
    self.assertEqual(caps["video"]["scenes"][0]["scene_id"], "text_or_image")
```

- [ ] Run the single test and verify it fails because `normalize_capabilities` does not exist.

- [ ] Implement helpers in `server.py`:

```python
def localized_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("zh", "en", "zh-TW"):
            text = value.get(key)
            if isinstance(text, str) and text:
                return text
        for text in value.values():
            if isinstance(text, str) and text:
                return text
    return ""
```

Add `normalize_ratios`, `normalize_image_models`, `normalize_video_models`, `normalize_video_scenes`, and `normalize_capabilities` following the design document.

- [ ] Run all tests.

## Task 3: Capability API Routes

- [ ] Add failing tests:

```python
def test_gateway_capabilities_requires_api_key_and_returns_models(self):
    account_id = self.seed_account()
    conn = server.db_conn()
    now = time.time()
    conn.execute("INSERT INTO api_keys(key,name,enabled,created_at) VALUES(?,?,1,?)", ("cap-key", "cap", now))
    conn.execute(
        "UPDATE accounts SET model_info_json=?, video_info_json=? WHERE id=?",
        (json.dumps(self.sample_image_info()), json.dumps(self.sample_video_info()), account_id),
    )
    conn.commit()
    conn.close()
    unauthorized = self.client.get("/v1/capabilities")
    authorized = self.client.get("/v1/capabilities", headers={"Authorization": "Bearer cap-key"})
    self.assertEqual(unauthorized.status_code, 401)
    self.assertEqual(authorized.status_code, 200)
    self.assertEqual(authorized.json()["image"]["models"][0]["name"], "Google Nano Banana 2")

def test_admin_model_capabilities_requires_admin(self):
    self.seed_account()
    unauthorized = self.client.get("/api/models/capabilities")
    authorized = self.client.get("/api/models/capabilities", headers=self.admin_headers())
    self.assertEqual(unauthorized.status_code, 401)
    self.assertEqual(authorized.status_code, 200)
```

- [ ] Implement:
  - `load_capabilities_from_pool()`
  - `GET /v1/capabilities`
  - `GET /api/models/capabilities`
  - `POST /api/models/refresh`

- [ ] Run all tests.

## Task 4: Admin UI Updates

- [ ] Add failing HTML test assertions:

```python
def test_admin_html_contains_credentials_and_capability_controls(self):
    html = server.ADMIN_HTML
    self.assertIn("/api/admin/credentials", html)
    self.assertIn("/api/models/capabilities", html)
    self.assertIn("changeCredentials", html)
    self.assertIn("loadCapabilities", html)
```

- [ ] Update settings tab:
  - Add credentials fields.
  - Remove generic `s-admin-pwd`.
  - Add `changeCredentials()` JS function.
  - Add `loadCapabilities()` and option-population helpers.
  - Call `loadCapabilities()` during `init()`.

- [ ] Run all tests.

## Task 5: Final Verification

- [ ] Run:

```bash
python -m unittest discover -s tests -p "*_tests.py"
python -m py_compile server.py banti_token_generator.py
git diff --check
```

- [ ] Confirm unauthorized smoke:

```bash
python - <<'PY'
from fastapi.testclient import TestClient
import server
server.init_db()
c = TestClient(server.app)
for method, path, body in [
    ("get", "/api/models/capabilities", None),
    ("post", "/api/admin/credentials", {"current_password": "x", "new_username": "a", "new_password": "b", "confirm_password": "b"}),
    ("get", "/v1/capabilities", None),
]:
    r = getattr(c, method)(path, json=body) if body is not None else getattr(c, method)(path)
    print(method.upper(), path, r.status_code)
PY
```

Expected output:

```text
GET /api/models/capabilities 401
POST /api/admin/credentials 401
GET /v1/capabilities 401
```
