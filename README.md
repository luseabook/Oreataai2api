# OreateAI Gateway / Pool Manager

当前状态：
- 已打通 Oreate 登录协议
- 已打通图片/视频配置接口
- 已打通 `/oreate/create/chat` 的 image/video 提交入口
- 已实现基础管理服务：账号导入、号池存储、图片/视频提交 API
- 结果流 / 资源 URL 抽取仍需继续补完
- 自动注册（YYDS 邮箱）链路尚未完成

## 文件
- `server.py` — FastAPI 服务，SQLite 持久化，管理页 `/admin`
- `config.example.json` — 配置模板
- `config.json` — 实际配置（首次运行可从 example 复制）
- `accounts.db` — SQLite 号池数据库（运行后自动生成）

## 运行
```bash
cp config.example.json config.json
# 编辑 config.json，填入 YYDS API Key
python server.py
```

默认监听：
- `http://127.0.0.1:8890`
- 管理页：`http://127.0.0.1:8890/admin`

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
`POST /oreate/create/chat`
```json
{
  "docId": "",
  "content": "a cute corgi astronaut on the moon, cinematic lighting",
  "chatMode": "aiImage",
  "modelName": "Google Nano Banana 2",
  "ratio": "16:9",
  "resolution": "4K",
  "jt": ""
}
```

### 生视频提交
`POST /oreate/create/chat`
```json
{
  "docId": "",
  "content": "a corgi astronaut gently waving on the moon",
  "chatMode": "aiVideo",
  "sceneId": "text_or_image",
  "modelName": "Seedance 2.0 Mini",
  "duration": 5,
  "resolution": "480",
  "ratio": "16:9",
  "jt": ""
}
```

## 当前缺口
- 自动注册：待补 `/passport/api/emailsignupin` + YYDS 收信 + `/passport/api/emailregisterconfirm`
- 结果流：已确认前端存在 SSE 管理器，但真实结果 URL / groupId 映射未完成
- 号池维护：基础结构已搭好，自动补号逻辑待实现

## 下一步
1. 先补自动注册链路
2. 再补结果流定位
3. 最后补号池自动维护
