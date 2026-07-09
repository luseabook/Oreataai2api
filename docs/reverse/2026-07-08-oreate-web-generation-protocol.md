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

## Upload Protocol

The upload-backed video scenes do not send local paths in `videoConfig`. The web first uploads the local file to Oreate/BOS-backed Google Storage, then places the returned object path in both `videoConfig` and `messages[0].attachments`.

Observed web flow:
1. Build `mFileList`:
   ```json
   [{"filename":"ref","fileExt":"png","size":1234}]
   ```
2. `POST /oreate/convert/getuploadbostoken`.
   - The home upload component adds `source: "aiImage"` for image/video extensions.
3. Read `KeyList[0].bucket`, `KeyList[0].objectPath`, and `KeyList[0].sessionkey`.
4. Start Google resumable upload:
   ```text
   POST https://storage.googleapis.com/upload/storage/v1/b/<bucket>/o?uploadType=resumable&name=<objectPath>
   Authorization: Bearer <sessionkey>
   ```
5. `PUT` the file bytes to the returned `Location`.
6. For media uploads, call `/oreate/convert/submit` with `fileName`, `fileExt`, `fileSize`, `bucket`, `object`, and `needEdit:false`.
7. Return a gateway attachment object with `fileName`, `fileExt`, `originSize`, `object`, `bosUrl`, `bosObjectPath`, and conversion metadata such as `docId`/`parseInfo` when present.

Gateway implementation:
- `/v1/uploads` performs the token exchange and Google resumable upload without browser automation.
- For media files, `/v1/uploads` now mirrors the web's `source:"aiImage"` token request and `convert/submit` follow-up before returning the attachment.
- `/v1/generate` accepts uploaded attachment objects in `image`, `first_frame`, `last_frame`, `reference_images`, `reference_videos`, `character_image`, and `motion_video`.
- The gateway rebuilds `messages[0].attachments` with the same `nke(files)` shape used by the web.
- Attachment fields are accepted only when they contain an uploaded object path such as `object`, `bosUrl`, or `bos_url`; filename-only placeholders are rejected before any upstream generation call.
- Scene configs use object paths:
  - `textOrImage.image`
  - `frameBased.firstFrame/lastFrame`
  - `reference.referenceImages/referenceVideos`
  - `motion.characterImage/motionVideo`

Security note:
- The temporary BOS `sessionkey` is used only inside `OreateClient.upload_file_bytes`.
- It is not persisted, returned by the API, or written into task payloads.

Static diff update on 2026-07-09:
- The earlier live video attempts uploaded bytes successfully, but the upload attachment was missing the web's media conversion step.
- The page upload code also uses `encodeURIComponent(objectPath)` for the Google resumable `name` parameter; the gateway now matches this encoding.
- `getVideoConfig()` conditionally clears `ratio`/`resolution` and omits `duration` when the selected model/scene capability exposes no values for those controls. The gateway now mirrors that behavior for normalized capability data.
- This fixed a concrete protocol mismatch. Later 2026-07-09 live validation superseded the early failure state for basic text-to-video and upload-backed `text_or_image`: both now produce real hydrated CDN MP4 assets through pure protocol replay.
- The early `100003` failures remain useful evidence that model-service failures can consume points. They do not by themselves disprove upload parity, because the later Pixverse V5 upload-backed run succeeded with the web-style upload and hydration flow.

## Current Gateway Behavior

Current `/v1/generate` behavior:
- Select an account from the pool.
- Validate requested model/ratio/resolution/duration/scene against cached capability data.
- Create a chat session with `/oreate/create/chat`.
- Submit generation to `/oreate/sse/stream`.
- Send prompt in `messages[0].content`.
- Send image options in `imageConfig`.
- Send video options in scene-specific `videoConfig`.
- Send upload-backed video attachments in `messages[0].attachments`.
- Include mirrored web fields: `jt`, `ua`, `js_env`, and `extra.deviceID/bid/user fields`.
- Parse SSE events and hydrate final resources from `/oreate/memory/getmessagelist`.

Remaining qualification gap:
- Text-to-image has been proven with real account protocol replay.
- Basic text-to-video has been proven with real account protocol replay and history hydration.
- Upload-backed `text_or_image` video has been proven with real account protocol replay, web-style BOS upload, conversion submit, role-aware attachment handling, and history hydration.
- Advanced upload-backed scenes `reference`, `frame_based`, and `motion` are implemented from static web evidence and unit-level protocol tests, but still require separate low-cost live success proof before claiming production-equivalent web parity.

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

Upload-backed video validation on 2026-07-09:
- Gateway path used: `/v1/uploads -> /v1/generate -> /oreate/create/chat -> /oreate/sse/stream`.
- Upload protocol result: three PNG uploads succeeded through `/oreate/convert/getuploadbostoken` plus Google Storage resumable upload. The returned object paths were accepted by gateway request construction.
- Account 1, `Seedance 1.5 Pro`, `text_or_image`, 480p, 5s, image attachment, estimated 7 points:
  - upload succeeded;
  - stream did not fail fast with `200002`;
  - final upstream error was `100003 call service error`;
  - account 1 had only `daily=3`, `bonus=5` in `/oreate/account/getpointdetail`.
- Account 1, `Seedance 2.0 Mini`, `text_or_image`, 480p, 5s, image attachment, estimated 20 points:
  - upload succeeded;
  - final upstream error was again `100003 call service error`;
  - account balance was insufficient for this estimate, so this run is not useful as a success proof.
- Account 12, capabilities refreshed, `Seedance 2.0 Mini`, `text_or_image`, 480p, 5s, image attachment, estimated 20 points:
  - before run: `/oreate/account/getpointdetail` returned `daily=27`, `bonus=100`;
  - upload succeeded;
  - stream held open for several minutes, then returned `100003 call service error`;
  - after run: point detail returned `daily=7`, `bonus=100`, meaning 20 points were consumed despite no asset being returned.

Interpretation:
- `200002` remains a request-contract rejection and has no observed point deduction.
- `100003` is different: the request appears to reach the model service layer and can consume points even when no video asset is produced.
- This was the interpretation immediately after the early failed upload-backed attempts. It is superseded for upload-backed `text_or_image` by the later Pixverse V5 success below, while the warning still applies to unproven advanced upload scenes.

Text-to-video validation on 2026-07-09:
- Account used: internal account id `2`.
- Model: `Seedance 1.5 Pro`.
- Scene: `text_or_image`.
- Ratio/resolution/duration/audio: `16:9`, `480`, `5`, `false`.
- `aiType`: `14001`.
- Estimated cost: `7`.
- Stream body matched the web shape: `clientType: "pc"`, `chatType: "aiVideo"`, `messages[0].attachments: []`, web `extra` mirror fields, `jt`, `ua`, `js_env`, and nested `videoConfig.textOrImage: {"image": ""}`.
- Stream headers used the video page context:
  - `accept: text/event-stream`
  - `Client-Type: pc`
  - `referer: https://www.oreateai.com/home/vertical/aiVideo/zh`
- SSE behavior:
  - HTTP `200`, `text/event-stream`.
  - events: `start`, then repeated `ping`.
  - no `end`, no `error`, no `generating` within the long read window.
- Point evidence:
  - before run: `daily=53`, `bonus=50`;
  - after stream abort/timeout: `daily=46`, `bonus=100`;
  - net is consistent with `-7` video cost plus `+50` first-use reward.
- History hydration:
  - chatId: `629f8ac016e542d03bab9b87`.
  - endpoint: `/oreate/memory/getmessagelist?pn=1&rn=30&chatID=629f8ac016e542d03bab9b87`.
  - first poll returned assistant content `generating video`, status `1`.
  - later poll returned `<video ... src="https://cdn.oreateai.com/aivideo/videodownload/1899992928.mp4">`.
  - extracted asset: `https://cdn.oreateai.com/aivideo/videodownload/1899992928.mp4`.

Critical text-to-video conclusion:
- The remaining basic text-to-video issue was not a hidden field.
- The gateway was waiting for the SSE stream to finish, but the web-visible video stream can keep pinging after the task is already accepted.
- Correct protocol handling is `create_chat -> sse/stream until terminal/error/read deadline -> poll getmessagelist by chatID until video asset, failure, or timeout`.
- This is a stateful session/hydration contract, not a browser-only dependency.

Upload-backed `text_or_image` video success validation on 2026-07-09:
- Account used: internal account id `12`.
- Upload input: generated local `512x512` PNG, uploaded through `/oreate/convert/getuploadbostoken`, Google Storage resumable upload, and `/oreate/convert/submit`.
- Upload artifact had:
  - Oreate object path under `aiimage/upload/...png`;
  - `docId`;
  - `parseInfo`.
- Model: `Pixverse V5`.
- Scene: `text_or_image`.
- Ratio/resolution/duration/audio: `1:1`, `360`, `5`, `false`.
- `aiType`: `14065`.
- Estimated cost: `5`.
- ChatId: `840f1cebeaff404810e64e93`.
- Stream behavior:
  - `start`, then repeated `ping`;
  - no immediate terminal video asset;
  - production stream logic treated this as `submitted`.
- First hydration exposed a gateway bug:
  - history had a `role=user` message containing the uploaded image attachment;
  - the old asset extractor counted that uploaded source image as a generated asset;
  - fix: `extract_generation_assets` now ignores `role=user` dictionaries.
- Continued hydration exposed a second gateway bug:
  - top-level history response had `status.code = 0` and `errMsg = "success"`;
  - the old history-error classifier treated any `errMsg` string as a failure;
  - fix: history failures now require an explicit non-zero code or `failReason`.
- Final hydration:
  - attempt count: `17`;
  - assistant message contained `<video ... src="https://cdn.oreateai.com/aivideo/videodownload/385175529.mp4">`;
  - extracted asset: `https://cdn.oreateai.com/aivideo/videodownload/385175529.mp4`.
- CDN verification:
  - `HEAD` status: `200`;
  - `content-type`: `video/mp4`;
  - `content-length`: `361797`.
- Point evidence:
  - before run: `daily=27`, `bonus=100`;
  - after final hydration: `daily=52`, `bonus=100`;
  - net `+25` is consistent with a `+30` daily/first-use grant and the selected `-5` video charge.

Critical upload-backed conclusion:
- Upload-backed `text_or_image` video is now proven live through pure protocol replay.
- The gateway must not classify user-uploaded source media as generated assets.
- The gateway must tolerate successful history envelopes with `errMsg="success"` while assistant content remains `generating video`.
- Remaining unproven upload-backed scenes are `reference`, `frame_based`, and `motion`.

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
- 2026-07-09, account 2, minimal text-to-video prompt, pure Python protocol replay.
- Video stream result: `start -> ping...`; no terminal `end` was observed, but history hydration returned a real MP4 CDN URL for logId `1899992928`.
- Gateway fix: video read-timeout/ping-only streams are treated as `submitted`, then `getmessagelist` is polled until the video asset appears or the hydration timeout is reached.
- 2026-07-09, account 12, uploaded-image-to-video prompt, pure Python protocol replay.
- Upload-backed video result: `start -> ping...`, then history hydration returned a real MP4 CDN URL for logId-like asset id `385175529`.
- Gateway fix: generated asset extraction ignores `role=user` uploads; history success envelopes are not treated as failures just because they contain `errMsg="success"`.

## Recommended Implementation Plan

1. Add protocol methods:
   - `create_chat_session(session, chat_type)`.
   - `stream_generation(session, chat_id, focus_id, chat_type, messages, image_config=None, video_config=None)`.
   - `parse_sse_events(response_iter)`.
   - `hydrate_generation_result(session, chat_id)` using `/oreate/memory/getmessagelist` with uppercase `chatID`.
   - `hydrate_generation_result_until_assets(session, chat_id)` for video streams that submit successfully but keep pinging.

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

8. Add upload protocol support:
   - `POST /oreate/convert/getuploadbostoken`.
   - Google Storage resumable upload initiation.
   - Google Storage byte upload to returned `Location`.
   - `/v1/uploads` API with API Key protection.
   - uploaded object normalization into web `nke(files)` message attachments.

9. Add regression tests:
   - image request builds `imageConfig` and calls `/oreate/sse/stream`.
   - video `text_or_image` builds `textOrImage`.
   - `frame_based` requires first/last frame.
   - `reference` applies input slot restrictions.
   - `motion` requires character image and motion video.
   - invalid model/resolution/duration rejected before upstream call.
   - SSE file frames become persisted task results.
   - history hydration extracts markdown/CDN file URLs from assistant messages.
   - upstream error frames classify nested `data.code`.
   - upload API returns both raw attachment and message attachment.
   - account health classification separates `200001`, `200002`, `212361`, and `110012`.

## Remaining Unknowns

- `chrome-devtools` first-pass evidence is blocked by local profile lock. `js-reverse` and direct HTTP evidence were used instead.
- Logged-in text-to-image is proven with pure protocol replay and hydrated CDN assets.
- Logged-in basic text-to-video is proven with pure protocol replay and hydrated CDN MP4 assets.
- Upload-backed `text_or_image` video is proven with pure protocol replay and hydrated CDN MP4 assets.
- Advanced upload-backed scenes `reference`, `frame_based`, and `motion` are implemented from static web evidence and unit-level protocol tests, but still need separate live success proof before being called production-equivalent.
- Account health/cooldown classification is implemented for known generation outcomes, but broader pool automation and replacement registration are still incomplete.

## Review Verdict

The current project is now a protocol-compatible image gateway, a protocol-compatible basic text-to-video gateway, and a protocol-compatible upload-backed `text_or_image` video gateway. Advanced upload-backed scenes still need separate live success proof.

Text-to-image generation now follows the web's two-stage `create_chat -> sse/stream -> getmessagelist` protocol, restores Banti `jt` and `__bid_n`, preserves the web `ZCe` mirror fields, and hydrates extensionless Oreate CDN image results from `messageList`. Text-to-video additionally handles the web behavior where SSE can keep pinging without `end` and the final MP4 appears only through history hydration. Upload-backed `text_or_image` video now uses the web-style BOS upload object flow, normalized message attachments, role-aware result extraction, and history polling until a real MP4 appears. Remaining production gaps are controlled proof for `reference`/`frame_based`/`motion` and full account-pool maintenance automation.
