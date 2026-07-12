'use strict';

const path = require('path');

async function readInput() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(chunk);
  }
  const text = Buffer.concat(chunks).toString('utf8');
  return JSON.parse(text);
}

function requirePuppeteer(nodeModulesPath) {
  if (!nodeModulesPath || !path.isAbsolute(nodeModulesPath)) {
    throw new Error('runtime.nodeModulesPath must be an absolute path');
  }
  return require(path.join(nodeModulesPath, 'puppeteer-core'));
}

async function run() {
  const input = await readInput();
  const runtime = input.runtime || {};
  const puppeteer = requirePuppeteer(runtime.nodeModulesPath);
  const baseUrl = String(input.baseUrl || 'https://www.oreateai.com').replace(/\/+$/, '');
  const chatType = input.chatType === 'aiVideo' ? 'aiVideo' : 'aiImage';
  const route = chatType === 'aiVideo'
    ? '/home/vertical/aiVideo/zh'
    : '/home/vertical/aiImage/zh';
  const executablePath = String(runtime.chromiumExecutable || '');
  if (!executablePath) throw new Error('runtime.chromiumExecutable is required');

  const browser = await puppeteer.launch({
    executablePath,
    headless: true,
    timeout: Number(runtime.navigationTimeoutMs || 90000),
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-blink-features=AutomationControlled',
      '--no-first-run',
      '--no-default-browser-check',
    ],
  });

  try {
    const page = await browser.newPage();
    const userAgent = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36';
    await page.setUserAgent(userAgent);
    await page.evaluateOnNewDocument(() => {
      Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    });
    await page.setCookie(
      {
        name: 'OUID',
        value: String(input.account?.ouid || ''),
        domain: '.oreateai.com',
        path: '/',
        secure: true,
        httpOnly: false,
      },
      {
        name: 'ouss',
        value: String(input.account?.ouss || ''),
        domain: '.oreateai.com',
        path: '/',
        secure: true,
        httpOnly: false,
      },
    );
    try {
      await page.goto(`${baseUrl}${route}`, {
        waitUntil: 'domcontentloaded',
        timeout: Number(runtime.navigationTimeoutMs || 90000),
      });
    } catch (error) {
      if (!String(page.url()).includes('oreateai.com')) throw error;
    }
    const requestedReadinessTimeoutMs = Number(runtime.readinessTimeoutMs);
    const readinessTimeoutMs = Number.isFinite(requestedReadinessTimeoutMs)
      && requestedReadinessTimeoutMs >= 5000
      ? requestedReadinessTimeoutMs
      : 60000;
    await page.waitForFunction(
      () => window.ParisFactory && typeof window.ParisFactory.create === 'function',
      {timeout: readinessTimeoutMs},
    );

    return await page.evaluate(async (request) => {
      const eventHasError = (value) => {
        if (!value || typeof value !== 'object') return false;
        const data = value.data && typeof value.data === 'object' ? value.data : {};
        const code = data.code || value.code || value.err;
        return value.event === 'error' || Boolean(code);
      };
      const headers = {
        'Content-Type': 'application/json',
        'Client-Type': 'pc',
        'Locale': 'zh-CN',
      };
      const safeJson = async (response) => {
        const text = await response.text();
        try {
          return JSON.parse(text);
        } catch (_) {
          return {raw: text.slice(0, 1000)};
        }
      };
      const errorFromBody = (body, fallback) => {
        const status = body && typeof body.status === 'object' ? body.status : {};
        const source = body && typeof body.error === 'object' ? body.error : status;
        return {
          code: String(source.code || source.errorCode || fallback || 'UPSTREAM_ERROR'),
          message: String(source.message || source.msg || body?.message || 'upstream request failed'),
        };
      };

      const userResponse = await fetch('/oreate/user/getuserinfo', {
        credentials: 'include',
        headers: {'Client-Type': 'pc', 'Locale': 'zh-CN'},
      });
      const userBody = await safeJson(userResponse);
      if (!userResponse.ok) {
        return {error: errorFromBody(userBody, userResponse.status)};
      }
      const userData = userBody && typeof userBody.data === 'object' ? userBody.data : userBody || {};
      const basicInfo = userData && typeof userData.basicInfo === 'object' ? userData.basicInfo : {};
      const vipInfo = userData && typeof userData.vipInfo === 'object' ? userData.vipInfo : {};
      const email = basicInfo.email || request.account.email || '';
      const vip = vipInfo.vipType == null ? '' : String(vipInfo.vipType);
      const regTs = basicInfo.createTime || '';

      const chatResponse = await fetch('/oreate/create/chat', {
        method: 'POST',
        credentials: 'include',
        headers,
        body: JSON.stringify({type: request.chatType, docId: ''}),
      });
      const chatBody = await safeJson(chatResponse);
      const chatData = chatBody && typeof chatBody.data === 'object' ? chatBody.data : chatBody || {};
      const chatId = chatData.chatId || '';
      const focusId = chatData.focusId || chatId;
      if (!chatResponse.ok || !chatId) {
        return {error: errorFromBody(chatBody, chatResponse.status)};
      }

      const banti = window.ParisFactory.create({
        sid: '2146',
        sak: '21a851acb0',
        timeout: 5000,
        bantiUrl: 'https://cdn.oreateai.com/static/v1/js/banti_21a851acb0_2025.js',
        bantiOptions: {
          reportTimeout: 200,
          bantiOrigin: 'https://banti.oreateai.com',
          ymgOrigin: 'https://banti.oreateai.com',
        },
      });
      const jt = await new Promise((resolve, reject) => {
        banti.sendBantiReport({subid: ''}, (error, response) => {
          if (error) return reject(new Error(String(error)));
          const value = response?.htj?.jt || '';
          if (!value) return reject(new Error('banti report did not return jt'));
          resolve(value);
        });
      });
      const cookieMap = {};
      for (const part of document.cookie.split(';')) {
        const index = part.indexOf('=');
        if (index < 0) continue;
        const name = decodeURIComponent(part.slice(0, index).trim());
        const value = decodeURIComponent(part.slice(index + 1).trim());
        cookieMap[name] = value;
      }
      const body = {
        type: 'chat',
        focusId,
        chatId,
        chatType: request.chatType,
        from: 'home',
        chatTitle: 'Unnamed Session',
        messages: [{
          role: 'user',
          content: request.prompt,
          attachments: request.attachments || [],
        }],
        isFirst: true,
        extra: {
          doc_name: '',
          module_name: 'gpt4o',
          email,
          vip,
          reg_ts: regTs,
          deviceID: cookieMap.OUID || '',
          bid: cookieMap.__bid_n || '',
        },
        clientType: 'pc',
        jt,
        ua: request.userAgent,
        js_env: 'h5',
      };
      if (request.imageConfig) body.imageConfig = request.imageConfig;
      if (request.videoConfig) body.videoConfig = request.videoConfig;

      const controller = new AbortController();
      const timeout = setTimeout(
        () => controller.abort(),
        Number(request.streamWaitMs || 60000),
      );
      const events = [];
      let completionReason = 'eof';
      let streamResponse;
      try {
        streamResponse = await fetch('/oreate/sse/stream', {
          method: 'POST',
          credentials: 'include',
          headers: {...headers, Accept: 'text/event-stream'},
          body: JSON.stringify(body),
          signal: controller.signal,
        });
        if (!streamResponse.ok) {
          const errorBody = await safeJson(streamResponse);
          return {error: errorFromBody(errorBody, streamResponse.status)};
        }
        const reader = streamResponse.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let done = false;
        while (!done) {
          const next = await reader.read();
          done = next.done;
          buffer += decoder.decode(next.value || new Uint8Array(), {stream: !done});
          const lines = buffer.split(/\r?\n/);
          buffer = lines.pop() || '';
          for (const line of lines) {
            if (!line.startsWith('data:')) continue;
            const raw = line.slice(5).trim();
            if (!raw || raw === '[DONE]') continue;
            try {
              const event = JSON.parse(raw);
              events.push(event);
              if (event?.event === 'end') {
                completionReason = 'end';
                done = true;
                break;
              }
              if (eventHasError(event)) {
                completionReason = 'error';
                done = true;
                break;
              }
            } catch (_) {
              events.push({event: 'message', data: raw});
            }
          }
        }
        if (done && completionReason === 'eof') completionReason = 'eof';
        try {
          await reader.cancel();
        } catch (_) {
          // The upstream may already have closed the stream.
        }
      } catch (error) {
        if (error?.name !== 'AbortError' || events.length === 0) throw error;
        completionReason = 'stream_wait_elapsed';
      } finally {
        clearTimeout(timeout);
      }

      const hasError = events.some(eventHasError);
      const status = hasError
        ? 'failed'
        : request.chatType === 'aiVideo' && completionReason !== 'end'
          ? 'submitted'
          : 'streamed';
      return {
        chat: {chatId, focusId},
        stream: {
          events,
          error: null,
          status,
          completion_reason: completionReason,
        },
      };
    }, {
      account: input.account,
      chatType,
      prompt: String(input.prompt || ''),
      imageConfig: input.imageConfig || null,
      videoConfig: input.videoConfig || null,
      attachments: Array.isArray(input.attachments) ? input.attachments : [],
      streamWaitMs: Number(runtime.streamWaitMs || 60000),
      userAgent,
    });
  } finally {
    await browser.close();
  }
}

run()
  .then((result) => {
    process.stdout.write(JSON.stringify(result));
  })
  .catch((error) => {
    process.stderr.write(error?.stack || String(error));
    process.exitCode = 1;
  });
