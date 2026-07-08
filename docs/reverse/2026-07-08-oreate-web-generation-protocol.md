# OreateAI Web Generation Protocol Analysis

Date: 2026-07-08
Target:
- `https://www.oreateai.com/home/vertical/aiImage`
- `https://www.oreateai.com/home/vertical/aiVideo`
- `https://www.oreateai.com/home/chat/aiVideo`

## Startup Gate

Environment:
- Python 3.11.14, Node, npm, curl, git available.
- `curl_cffi` available.
- `iv8` missing.
- `js-reverse` available and used for page, source, network, initiator, and in-page protocol inspection.
- `chrome-devtools` is blocked by a local profile lock:
  `The browser is already running for C:\Users\Administrator\.cache\chrome-devtools-mcp\chrome-profile`.

Classification:
- Primary family: `session-gated`.
- Secondary characteristic: `transport-wrapper`.
- Reason: public capability/config routes are readable anonymously, but generation is a stateful chat/SSE flow using a chat session, cookies, generated `jt`, browser/user metadata, and streamed server events.

Delivery intent:
- Final gateway implementation should be pure Python HTTP protocol.
- A small local JS helper is acceptable only if needed for `jt` restoration, but the current project already has `banti_token_generator.py`.
- Browser-backed fetch, browser profiles, Playwright/Selenium/CDP page-driving, and submit-through-browser flows are not acceptable as final runtime.

## Recon

Final landing URLs:
- Image vertical page redirects to `/home/vertical/aiImage/zh`.
- Video vertical page redirects to `/home/vertical/aiVideo/zh`.

Page type:
- SPA/CSR.

Useful data source:
- XHR for model and scene capabilities.
- Fetch/EventSource-style POST for generation stream.
- JavaScript bundles for request construction.

Relevant bundles:
- `https://cdn.oreateai.com/static/oreatesea/assets/index-CpcStoMO.js`
- `https://cdn.oreateai.com/static/oreatesea/assets/index-BXaH05qK.js`
- `https://cdn.oreateai.com/static/oreatesea/assets/index-C1B32EFx.js`

## Public Capability Requests

Image capability:
- `GET /oreate/img/getmodelconfig`
- Status: `status.code = 0`
- Python protocol baseline: HTTP 200, 4 factories, 11 models.

Video model capability:
- `GET /oreate/aivideo/getmodelconfigv3`
- Status: `status.code = 0`
- Python protocol baseline: HTTP 200, 15 models.
- Response contains model description, duration, resolution, ratio, audio support, size modification support, point cost, and `aiType`.

Video scene capability:
- `GET /oreate/aivideo/getsceneconfig`
- Status: `status.code = 0`
- Python protocol baseline: HTTP 200, 4 scenes:
  - `text_or_image`
  - `frame_based`
  - `reference`
  - `motion`

Anonymous session observations:
- `GET /oreate/user/getuserinfo` returns `200001 user not login`.
- `GET /oreate/memory/getchatlist?pn=1&rn=30&updateTime=0` returns `200001 user not login`.
- `POST /oreate/create/chat` with `{"type":"aiImage","docId":""}` or `{"type":"aiVideo","docId":""}` returns HTTP 200 and a `chatId` even in anonymous context.

Conclusion:
- Model lists, video descriptions, resolutions, ratios, durations, scenes, restrictions, and cost mappings are not hidden behind login.
- Generation session creation is separable from login/user info.
- Actual generation still requires the chat/SSE protocol path.

## Real Request Candidates

### Capability Discovery

Requests:
- `GET /oreate/img/getmodelconfig`
- `GET /oreate/aivideo/getmodelconfigv3`
- `GET /oreate/aivideo/getsceneconfig`

Key headers observed:
- `accept: application/json, text/plain, */*`
- `locale: zh-CN`
- `client-type: pc`
- `referer: https://www.oreateai.com/home/vertical/aiVideo/zh`

Key cookies:
- Anonymous `OUID` is present.
- Login cookie is absent in this capture.

Decode needed:
- None. Responses are JSON.

### Chat Session Creation

Request:
- `POST /oreate/create/chat`

Web wrapper:
```js
function ivt(e) {
  e.docId = (e?.docId) || "";
  return tn.post("/oreate/create/chat", e);
}
```

Observed web call from chat page:
```js
const De = Wt(s.mode);
const We = await ns({ type: De });
a.setBaseChatInfo({
  chatId: We.chatId || "",
  chatType: De,
  focusId: We.focusId || "",
  from: "home"
});
```

Protocol shape:
```json
{
  "type": "aiVideo",
  "docId": ""
}
```

Important correction:
- The current gateway sends prompt/model fields to `/oreate/create/chat`.
- The web page uses `/oreate/create/chat` only to create a chat session.
- Prompt/model/options are sent later through `/oreate/sse/stream`.

### Generation Stream

Request:
- `POST /oreate/sse/stream`

Source evidence:
```js
async send(e) {
  const a = i ? "/oreate/agentskill/stream" : "/oreate/sse/stream";
  const s = this.baseChatInfo.chatId;
  const c = {
    aiImage: { key: "imageConfig", getConfig: () => Dre().submitConfig },
    aiVideo: { key: "videoConfig", getConfig: () => zTe().getVideoConfig() }
  }[this.baseChatInfo.chatType];
  ...
  const d = Object.assign({}, Hu(this.baseChatInfo), u, {
    extra: { doc_name: "", module_name: "gpt4o" }
  });
  this.SSEInstance.startSSE(a, s, {
    body: fre(d),
    needMirror: true,
    ...
  });
}
```

`startSSE` wrapper:
```js
let headers = {
  "Content-Type": "application/json",
  locale: D0 || "en-US",
  "Client-Type": t0() ? P9.WAP : P9.PC
};
let h = body;
if (needMirror) {
  const v = await ZCe("", 300);
  h = merge(v, body);
}
fetchEventSource(url, {
  method: "POST",
  headers,
  body: JSON.stringify(h)
});
```

`ZCe` mirror fields:
```js
{
  jt,
  ua: window.navigator.userAgent,
  js_env: "h5",
  extra: {
    email,
    vip,
    reg_ts,
    deviceID: cookie("OUID"),
    bid: cookie("__bid_n")
  }
}
```

Base generation body:
```json
{
  "type": "chat",
  "focusId": "<chatId-or-focusId>",
  "chatId": "<chatId>",
  "chatType": "aiVideo",
  "from": "home",
  "chatTitle": "Unnamed Session",
  "messages": [
    {
      "role": "user",
      "content": "<prompt>",
      "attachments": []
    }
  ],
  "videoConfig": {},
  "isFirst": true,
  "extra": {
    "doc_name": "",
    "module_name": "gpt4o"
  },
  "clientType": "pc",
  "jt": "<local banti token>",
  "ua": "<user-agent>",
  "js_env": "h5"
}
```

Stream response handling:
- Each SSE message has JSON in `event.data`.
- The client parses `JSON.parse(v.data)`.
- Important events in source: `start`, `generating`, `end`, `error`.
- `generating` may contain `data.result`; the client normalizes it to `data.content` when needed.
- Result file cards are parsed from `result` objects with `type: "file"` and `metadata.files`.

## Image Config

Web image store:
```js
submitConfig(state) {
  return {
    modelName: state.modelName,
    ratio: state.size,
    resolution: state.resolution
  };
}
```

Example public models:
- `Google Nano Banana 2 Lite`
  - description: faster lightweight model.
  - resolution: `["1K"]`
  - ratios: `1:1`, `2:3`, `3:2`, `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9`, `21:9`
- `Google Nano Banana 2`
  - description: flagship 4K high resolution.
  - resolution: `["4K", "2K", "1K"]`
- `Google Nano Banana`
  - `resolution: null`
  - point cost uses an empty resolution string.

Gateway implication:
- A null/empty image resolution list can be valid for some models.
- Validation should distinguish "model supports no explicit resolution selection" from "capability data missing".

## Video Config

Scene enum:
```js
text_or_image
frame_based
reference
motion
```

Video config fetch:
```js
const [models, scenes] = await Promise.all([
  getmodelconfigv3(),
  getsceneconfig()
]);
```

The web page computes available controls from:
- selected scene,
- selected model,
- uploaded file slot counts,
- scene/model restrictions,
- `paramsOverride`,
- `slotRules`,
- `pointCostImage`,
- `pointCostReference`,
- `pointCostMotion` when present.

`getVideoConfig()` output:

Common fields:
```json
{
  "modelName": "Seedance 2.0 Mini",
  "ratio": "16:9",
  "resolution": "480",
  "duration": 5,
  "isAudio": false,
  "aiType": 14198,
  "scene": "text_or_image"
}
```

Text/image-to-video:
```json
{
  "...common": "...",
  "textOrImage": {
    "image": "<bos object or empty string>"
  }
}
```

First/last-frame video:
```json
{
  "...common": "...",
  "frameBased": {
    "firstFrame": "<bos object>",
    "lastFrame": "<bos object>"
  }
}
```

Reference-to-video:
```json
{
  "...common": "...",
  "reference": {
    "referenceImages": ["<bos object>"],
    "referenceVideos": ["<bos object>"],
    "refDuration": "2-5",
    "refTotalDuration": 5,
    "keepOriginalSound": false
  }
}
```

Motion mimicry:
```json
{
  "...common": "...",
  "motion": {
    "characterImage": "<bos object>",
    "motionVideo": "<bos object>",
    "motDuration": 8,
    "keepOriginalSound": false
  }
}
```

Attachment conversion:
```js
function nke(files = []) {
  return files.map(file => ({
    bos_url: file.bosUrl || file.object,
    docId: file.docId,
    doc_title: file.fileName,
    doc_type: file.fileExt,
    size: file.originSize,
    bosUrl: file.bosUrl || file.object,
    flag: "upload",
    type: "file",
    status: 1,
    videoDurationSec: file.videoDurationSec
  }));
}
```

Gateway implication:
- A flat video payload with `sceneId/modelName/duration/resolution/ratio` is not web-equivalent.
- The gateway needs scene-specific nested config and attachment normalization.
- `aiType` should be derived from the same cost table the web uses; it is not optional for maximum parity.

## Current Gateway Gap

Current `/v1/generate` behavior:
- Select account.
- Build a flat image/video payload.
- Call `CLIENT.create_chat(s, payload)`.
- Save task as `created`.

Observed gap:
- `create_chat` is session creation, not generation.
- The prompt should be in `messages[0].content`, not `create_chat.content`.
- Image options should be in `imageConfig`.
- Video options should be in `videoConfig`.
- The actual submit endpoint should be `/oreate/sse/stream`.
- Request body should include mirrored fields: `jt`, `ua`, `js_env`, and `extra.deviceID/bid/user fields`.
- Result completion should be parsed from SSE frames or hydrated later from history/message endpoints.

This explains why the project still feels unqualified as an image/video gateway even after adding capability lists:
- It can list and validate some options.
- It cannot yet reproduce the web generation path.
- It cannot yet return final generated asset URLs reliably.
- It does not model video scene modes, uploads, reference files, motion mimicry, audio, original sound, or `aiType`.

## What Solves The User-Visible Problems

### "No model list"

Already solvable from public protocol:
- Image: `/oreate/img/getmodelconfig`
- Video models: `/oreate/aivideo/getmodelconfigv3`
- Video scenes: `/oreate/aivideo/getsceneconfig`

Recommended external API:
- Keep `/v1/capabilities`.
- Add scene-specific video capability output:
  - model availability per scene,
  - model descriptions,
  - supported ratios,
  - supported resolutions,
  - supported durations,
  - audio options,
  - keep-original-sound options,
  - upload slot restrictions,
  - estimated point cost / `aiType` combinations.

### "Video has no description/resolution selection"

The web config already has this data:
- `description.zh/en/...`
- `videoResolution`
- `videoSize`
- `duration`
- `supportAudio`
- `supportModifySize`
- `restrictions`

Frontend/backend should stop treating video as one flat model list and should render controls from `scene + model + restrictions`.

### "Make API output match web"

Required generation flow:

1. Ensure an Oreate session with `OUID` and `ouss` when authenticated generation is required.
2. `POST /oreate/create/chat` with `{"type":"aiImage|aiVideo","docId":""}`.
3. Build `messages`.
4. Build `imageConfig` or `videoConfig`.
5. Build mirror fields:
   - `clientType: "pc"`
   - `jt`
   - `ua`
   - `js_env: "h5"`
   - `extra.email/vip/reg_ts/deviceID/bid`
6. `POST /oreate/sse/stream` and parse SSE events.
7. Persist stream IDs:
   - `chatId`
   - `message_id`
   - `equery_id`
   - `groupId/messageGroupID`
   - final files/resources when present.
8. Use history endpoints as fallback hydration if stream parsing misses final files.

## Real Account Validation

Validation date:
- 2026-07-09 Asia/Shanghai.

Account pool:
- `accounts.db` contains 25 `verified` accounts with `OUID` and `ouss` fields.
- Current session check: accounts 1-24 return `userinfo.status.code = 0` and `isLogin = true`.
- Account 25 currently returns `userinfo.status.code = 200001` (`user not login`), while the point endpoint still returns `restPoint = 50`.
- Current point snapshot:
  - account 1: 35
  - accounts 2-10, 13-14, 16-24: 80
  - accounts 11, 12, 15: 127
  - account 25: 50

Confirmed real image outputs:
- Accounts 11, 12, and 15 previously accepted a web-captured image generation body and produced real CDN assets.
- Accepted stream shape: `start -> generating -> end`.
- Result hydration endpoint:
  - `GET /oreate/memory/getmessagelist?pn=1&rn=30&chatID=<chatID>`
  - `chatID` is case-sensitive. Lowercase `chatId` returns `200002 params error`.
- The assistant messages contain markdown image content with CDN URLs.
- CDN verification:
  - account 11: HTTP 200, `image/jpeg`, 512x512.
  - account 12: HTTP 200, `image/jpeg`, 512x512.
  - account 15: HTTP 200, `image/jpeg`, 512x512.

Point accounting observation:
- Before the successful accepted samples, the relevant accounts read as `restPoint = 80`.
- After accepted generation and history hydration, accounts 11/12/15 read as `restPoint = 127`.
- User-provided point ledger from another account explains this pattern:
  - welcome/first-use style grants can add `+50`.
  - daily check-in can add `+30`.
  - Agent image generation costs observed: `1K = -3`, `2K = -4`.
  - AI video generation costs observed: `-40` and `-70` in the sample ledger.
- Therefore the observed `80 -> 127` is consistent with a first generation reward `+50` and a 1K image charge `-3`, net `+47`.
- The asset evidence proves real generation, and the likely actual image cost for the accepted 1K samples is `3` points, even though the visible balance increased.

Bulk 25-account replay evidence:
- A pure Python hand-built body with:
  - model: `Google Nano Banana 2 Lite`
  - ratio: `1:1`
  - resolution: `1K`
  - `jt: ""`
  - web-like `ua/js_env/extra`
- Result:
  - accounts 1-24: login OK and `/oreate/create/chat` OK, but `/oreate/sse/stream` rejected with:

```text
data: {"event":"start",...}
data: {"event":"error",...,"data":{"code":200002,"msg":"params error"}}
data: {"event":"end",...}
```

  - History immediately after these failures returns `status.code = 110012` and no messages.
  - Point delta for all rejected accounts: `0`.
  - account 25 was skipped for generation because login state was invalid.

Earlier replay evidence:
- A browser-captured body replayed through pure Python produced `212361 spam user` for many accounts.
- The same capture class accepted accounts 11/12/15 at least once and created real assets.
- This means:
  - `create_chat` is solved.
  - result hydration is solved for image generation.
  - generation body parity is not fully solved by hand-writing visible fields.
  - account generation health is not equivalent to login health.

Operational implications:
- Gateway account selection must track at least:
  - login health (`200001 user not login`),
  - parameter/protocol rejection (`200002 params error`),
  - upstream generation risk (`212361 spam user`),
  - empty history after stream rejection (`110012`),
  - successful asset hydration.
- Accounts that hit `212361` or repeated `200002` on generation should be cooled down or excluded from generation, even if `getuserinfo` and `getrestpoints` still work.
- The production generator must preserve exact web body construction and should store the raw parsed SSE event summary for debugging.

## Implementation Decision

Delivery shape:
- Python protocol client.
- Python calls a local Node helper `banti_jt_helper.js` for current Banti `jt` restoration.
- The old pure-Python `banti_token_generator.py` encoder is now an emergency fallback only; it does not obtain the live server-issued `/dr` token by itself.
- No browser dependency in production.

Python responsibilities:
- HTTP session and cookies.
- Capability fetch.
- Capability normalization and validation.
- Chat session creation.
- Generation payload construction.
- SSE request and event parsing.
- Result persistence and history hydration.

Possible JS helper responsibility:
- `jt` and Banti cookie restoration only. It runs `banti_raw.js` in a local `vm` context with stubbed browser APIs, lets the SDK issue `/dr`, and returns the final `31$...` token plus SDK-written cookies such as `__bid_n` to Python.
- Verification on 2026-07-09: helper output decodes to `v = 1.14.3.1`, `to = 200`, and non-empty server-issued `j`; helper also produces a current `__bid_n`.

## 200002 Params Error Interpretation

`200002 params error` means the upstream accepted the HTTP route and chat session but rejected the generation body contract for `/oreate/sse/stream`.

It is not a balance/credit error:
- rejected streams return `start -> error(code=200002) -> end`;
- no points are deducted;
- history may later return `110012` because no valid generated message was persisted.

Confirmed root causes for the hand-built requests:
- old local `jt` used `v=10617531`, `to=5000`, `i=0`, and empty `j`;
- live web uses Banti SDK `1.14.3.1`, `/dr` server token, and commonly `to=200`;
- web `ZCe` merges `extra.email`, `extra.vip`, `extra.reg_ts`, `extra.deviceID`, and `extra.bid` into the stream body; the gateway previously sent empty `vip/reg_ts` and dropped SDK-written `__bid_n`;
- video payload must use nested scene config such as `videoConfig.textOrImage`, not flat `sceneId/modelName/duration/resolution` fields.

Fix verification:
- 2026-07-09, account 11, minimal 1K image prompt, pure Python protocol replay.
- Stream result: `start -> ping -> ping -> generating -> end`, no `200002`.
- History endpoint returned `data.messageList` with an extensionless Oreate image CDN URL.
- Asset extraction had to support `data.messageList` and extensionless `https://cdn.oreateai.com/aiimage/...` result URLs.

## Recommended Implementation Plan

1. Add protocol methods:
   - `create_chat_session(session, chat_type)`.
   - `stream_generation(session, chat_id, focus_id, chat_type, messages, image_config=None, video_config=None)`.
   - `parse_sse_events(response_iter)`.
   - `hydrate_generation_result(session, chat_id)` using `/oreate/memory/getmessagelist` with uppercase `chatID`.

2. Preserve the old `create_chat` method temporarily, but stop using it for `/v1/generate`.

3. Add generation request schemas:
   - image: `model_name`, `ratio`, `resolution`, optional reference uploads.
   - video: `scene_id`, `model_name`, `ratio`, `resolution`, `duration`, `is_audio`, `keep_original_sound`, `ai_type`, and scene-specific attachment fields.

4. Add capability compiler:
   - compile scene-specific video options from `models + scenes + restrictions`.
   - calculate valid `aiType` and point cost for the chosen option.
   - preserve raw restrictions for callers that want exact UI parity.

5. Implement `/v1/generate` as web-compatible two-stage flow:
   - create chat session,
   - stream generation,
   - save task with stream status and final assets when available.

6. Add `/v1/tasks/{id}` status/result semantics:
   - `queued/submitted/streaming/completed/failed`.
   - include final asset URLs and raw upstream IDs.

7. Add upstream error classification and account health:
   - `200001`: session invalid.
   - `200002`: protocol/body params rejected.
   - `212361`: upstream spam/risk gate.
   - `110012` on hydration: no persisted messages for the chat.
   - repeated generation rejection should increment account generation failure count and set cooldown.

8. Add regression tests:
   - image request builds `imageConfig` and calls `/oreate/sse/stream`.
   - video `text_or_image` builds `textOrImage`.
   - `frame_based` requires first/last frame.
   - `reference` applies input slot restrictions.
   - `motion` requires character image and motion video.
   - invalid model/resolution/duration rejected before upstream call.
   - SSE file frames become persisted task results.
   - history hydration extracts markdown/CDN file URLs from assistant messages.
   - upstream error frames classify nested `data.code`.

## Remaining Unknowns

- `chrome-devtools` first-pass evidence is blocked by local profile lock. `js-reverse` and direct HTTP evidence were used instead.
- Logged-in text-to-image is proven with pure protocol replay and hydrated CDN assets.
- A controlled logged-in text-to-video success is still needed before video generation is marked production-equivalent.
- Need account health/cooldown implementation before using a 25-account pool for production generation.
- Need upload protocol analysis for turning local files into `bosUrl/object/docId` records before image-to-video/reference/motion modes can be complete.

## Review Verdict

The current project is now close to a web-equivalent image gateway, but not yet a complete image/video gateway.

Text-to-image generation now follows the web's two-stage `create_chat -> sse/stream -> getmessagelist` protocol, restores Banti `jt` and `__bid_n`, preserves the web `ZCe` mirror fields, and hydrates extensionless Oreate CDN image results from `messageList`. Remaining production gaps are controlled text-to-video proof, upload-backed video scenes, and account-pool health automation for spam/risk outcomes such as `212361`.
